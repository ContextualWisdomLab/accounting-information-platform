"""One-shot normalizer for the observed PR #2 period-open and reversal RED boundaries."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _replace_once(path: str, old: str, new: str) -> None:
    text = _read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one repair boundary in {path}, found {count}")
    _write(path, text.replace(old, new, 1))


def _replace_definition(path: str, pattern: str, replacement: str) -> None:
    text = _read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise RuntimeError(f"expected exactly one definition boundary in {path}, found {count}")
    _write(path, updated)


def _migration() -> str:
    return """BEGIN;

CREATE TABLE accounting_integration.fiscal_period_open_command (
    fiscal_period_open_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    period_open_idempotency_key text NOT NULL
        CHECK (btrim(period_open_idempotency_key) <> ''),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    requested_period_start_date date,
    requested_period_end_date date,
    command_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),
    UNIQUE (tenant_account_id, period_open_idempotency_key),
    UNIQUE (tenant_account_id, fiscal_period_open_command_id),
    CHECK (
        (requested_period_start_date IS NULL AND requested_period_end_date IS NULL)
        OR (
            requested_period_start_date IS NOT NULL
            AND requested_period_end_date IS NOT NULL
            AND requested_period_end_date >= requested_period_start_date
        )
    )
);

ALTER TABLE accounting_integration.fiscal_period_open_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.fiscal_period_open_command FORCE ROW LEVEL SECURITY;

CREATE POLICY fiscal_period_open_command_isolation
    ON accounting_integration.fiscal_period_open_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

CREATE OR REPLACE FUNCTION accounting_integration.reject_period_open_command_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'fiscal period open command evidence is immutable (command_evidence_immutable)'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER fiscal_period_open_command_immutable
    BEFORE UPDATE OR DELETE ON accounting_integration.fiscal_period_open_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.reject_period_open_command_mutation();

REVOKE ALL ON accounting_integration.fiscal_period_open_command FROM PUBLIC;

COMMIT;
"""


def _repair_accept() -> None:
    replacement = '''def accept_period_open(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Open one fiscal period for *tenant_reference* with immutable command identity."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "period open payload must be a JSON object. "
            "Supply a period-open command, then retry the period open."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "open tenant_reference does not match the bound tenant. "
            "Call accept_period_open with that tenant_reference, then retry."
        )
    legal_entity_reference = str(payload.get("legal_entity_reference") or "")
    period_code = _period_code_from_reference(
        str(payload.get("fiscal_period_reference") or payload.get("period_code") or "")
    )
    if not legal_entity_reference or not period_code:
        raise AccountingValidationError(
            "legal_entity_reference and fiscal_period_reference are required. "
            "Supply those period-open fields, then retry the period open."
        )
    idempotency_key = payload.get("idempotency_key")
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key
        or idempotency_key != idempotency_key.strip()
    ):
        raise AccountingValidationError(
            "idempotency_key is required and must be a canonical non-empty string. "
            "Supply the period-open command key, then retry the period open."
        )
    source_payload_hash = payload.get("source_payload_hash")
    if not isinstance(source_payload_hash, str) or not _HASH_PATTERN.fullmatch(
        source_payload_hash
    ):
        raise AccountingValidationError(
            "source_payload_hash must be a canonical sha256 digest. "
            "Supply the immutable period-open command hash, then retry the period open."
        )
    start_text = str(payload.get("period_start_date") or "")
    end_text = str(payload.get("period_end_date") or "")
    period_start_date = (
        _parse_period_date(start_text, "period_start_date") if start_text else None
    )
    period_end_date = _parse_period_date(end_text, "period_end_date") if end_text else None
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.open_fiscal_period(
        legal_entity_reference,
        period_code,
        period_start_date,
        period_end_date,
        idempotency_key=idempotency_key,
        source_payload_hash=source_payload_hash,
    )


'''
    _replace_definition(
        "src/accounting_information_platform/accept.py",
        r"^def accept_period_open\(.*?(?=^def lookup_fiscal_period\()",
        replacement,
    )


def _repair_reversal_order() -> None:
    old = '''        if reversal_date < original.accounting_date:\n            raise AccountingValidationError(\n                "reversal date must not precede the original journal accounting date"\n            )\n        if not policy.permits(reversal_date):\n            raise AccountingValidationError("reversal date belongs to a closed fiscal period")\n        if (\n            original.legal_entity_reference != policy.legal_entity_reference\n            or original.accounting_book_reference != policy.accounting_book_reference\n        ):\n            raise AccountingValidationError("reversal policy scope does not match original journal")\n        reversal_reference = f"{journal_reference}:reversal"\n        occupant = self._journals.get(\n            self._tenant_cache_key(original.tenant_reference, reversal_reference)\n        )\n        if occupant is not None:\n            if occupant.reversal_of_journal_reference != journal_reference:\n                raise AccountingValidationError(\n                    "posted journal is immutable. Reverse the existing journal, then post a replacement."\n                )\n            if occupant.reversal_idempotency_key != command_key:\n                raise AccountingValidationError(\n                    "journal is already reversed. Use the existing reversal receipt, then retry."\n                )\n            if occupant.source_payload_hash != command_hash:\n                raise IdempotencyConflictError(\n                    "reversal idempotency key was already used with a different payload"\n                )\n            receipt = self._receipt_for_posted_journal(occupant)\n            self._reversal_receipts[reversal_key] = receipt\n            self._reversal_command_evidence[reversal_key] = (\n                command_key,\n                journal_reference,\n                command_hash,\n            )\n            return receipt\n'''
    new = '''        if (\n            original.legal_entity_reference != policy.legal_entity_reference\n            or original.accounting_book_reference != policy.accounting_book_reference\n        ):\n            raise AccountingValidationError("reversal policy scope does not match original journal")\n        reversal_reference = f"{journal_reference}:reversal"\n        occupant = self._journals.get(\n            self._tenant_cache_key(original.tenant_reference, reversal_reference)\n        )\n        if occupant is not None:\n            if occupant.reversal_of_journal_reference != journal_reference:\n                raise AccountingValidationError(\n                    "posted journal is immutable. Reverse the existing journal, then post a replacement."\n                )\n            if occupant.reversal_idempotency_key != command_key:\n                raise AccountingValidationError(\n                    "journal is already reversed. Use the existing reversal receipt, then retry."\n                )\n            if occupant.source_payload_hash != command_hash:\n                raise IdempotencyConflictError(\n                    "reversal idempotency key was already used with a different payload"\n                )\n            receipt = self._receipt_for_posted_journal(occupant)\n            self._reversal_receipts[reversal_key] = receipt\n            self._reversal_command_evidence[reversal_key] = (\n                command_key,\n                journal_reference,\n                command_hash,\n            )\n            return receipt\n        if reversal_date < original.accounting_date:\n            raise AccountingValidationError(\n                "reversal date must not precede the original journal accounting date"\n            )\n        if not policy.permits(reversal_date):\n            raise AccountingValidationError("reversal date belongs to a closed fiscal period")\n'''
    _replace_once("src/accounting_information_platform/core.py", old, new)


def _repair_persistence() -> None:
    replacement = '''    def open_fiscal_period(
        self,
        legal_entity_reference: str,
        period_code: str,
        period_start_date: date | None = None,
        period_end_date: date | None = None,
        *,
        idempotency_key: str,
        source_payload_hash: str,
    ) -> dict[str, object]:
        """Insert or replay one fiscal-period-open command from durable evidence."""
        if not legal_entity_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference and fiscal_period_reference are required. "
                "Supply those period-open fields, then retry the period open."
            )
        command_key = idempotency_key.strip()
        if not command_key or command_key != idempotency_key:
            raise AccountingValidationError(
                "period-open idempotency_key must be a canonical non-empty string. "
                "Supply the original command key, then retry the period open."
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", source_payload_hash) is None:
            raise AccountingValidationError(
                "period-open source_payload_hash must be a canonical sha256 digest. "
                "Supply the immutable command hash, then retry the period open."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(connection, f"period-open:{command_key}")
            self._acquire_command_lock(connection, f"period:{period_code}")
            legal_entity_id, _functional_currency = self._load_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the period open",
            )
            prior = connection.execute(
                """
                SELECT period_open_command.legal_entity_id,
                       fiscal_period.period_code,
                       period_open_command.requested_period_start_date,
                       period_open_command.requested_period_end_date,
                       fiscal_period.period_start_date,
                       fiscal_period.period_end_date,
                       period_open_command.source_payload_hash
                FROM accounting_integration.fiscal_period_open_command AS period_open_command
                JOIN accounting_core.fiscal_period AS fiscal_period
                  ON fiscal_period.tenant_account_id = period_open_command.tenant_account_id
                 AND fiscal_period.fiscal_period_id = period_open_command.fiscal_period_id
                WHERE period_open_command.tenant_account_id = %s
                  AND period_open_command.period_open_idempotency_key = %s
                """,
                (tenant_id, command_key),
            ).fetchone()
            if prior is not None:
                (
                    prior_legal_entity_id,
                    prior_period_code,
                    prior_requested_start,
                    prior_requested_end,
                    stored_start_date,
                    stored_end_date,
                    prior_source_hash,
                ) = prior
                if (
                    prior_legal_entity_id != legal_entity_id
                    or prior_period_code != period_code
                    or prior_requested_start != period_start_date
                    or prior_requested_end != period_end_date
                    or prior_source_hash != source_payload_hash
                ):
                    raise IdempotencyConflictError(
                        "period-open idempotency key was already used with a different payload"
                    )
                return self._period_open_document(
                    legal_entity_reference,
                    period_code,
                    stored_start_date,
                    stored_end_date,
                    replayed=True,
                )

            existing = self._load_period_state(connection, tenant_id, period_code)
            replayed = existing is not None
            if existing is not None:
                period_id, current_status, stored_start_date, stored_end_date = existing
                if current_status != "open":
                    raise AccountingValidationError(
                        f"Fiscal period {period_code} is {current_status}. "
                        "Closed periods cannot be reopened. Open a later period, "
                        "then retry the period open."
                    )
                if (
                    period_start_date is not None
                    and period_start_date != stored_start_date
                ) or (
                    period_end_date is not None and period_end_date != stored_end_date
                ):
                    raise AccountingValidationError(
                        "period-open dates do not match the already-open fiscal period. "
                        "Supply its existing dates or omit both dates, then retry."
                    )
            else:
                if period_start_date is None or period_end_date is None:
                    raise AccountingValidationError(
                        "period_start_date and period_end_date are required. "
                        "Supply those fiscal_period dates, then retry the period open."
                    )
                if period_end_date < period_start_date:
                    raise AccountingValidationError(
                        "period_end_date must be on or after period_start_date. "
                        "Supply a valid date range, then retry the period open."
                    )
                calendar_id = self._require_tenant_calendar(connection, tenant_id)
                period_id = connection.execute(
                    """
                    INSERT INTO accounting_core.fiscal_period (
                        tenant_account_id, fiscal_calendar_id, period_code,
                        period_start_date, period_end_date, period_status_code
                    )
                    VALUES (%s, %s, %s, %s, %s, 'open')
                    RETURNING fiscal_period_id
                    """,
                    (
                        tenant_id,
                        calendar_id,
                        period_code,
                        period_start_date,
                        period_end_date,
                    ),
                ).fetchone()[0]
                stored_start_date = period_start_date
                stored_end_date = period_end_date

            connection.execute(
                """
                INSERT INTO accounting_integration.fiscal_period_open_command (
                    tenant_account_id,
                    legal_entity_id,
                    fiscal_period_id,
                    period_open_idempotency_key,
                    source_payload_hash,
                    requested_period_start_date,
                    requested_period_end_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    period_id,
                    command_key,
                    source_payload_hash,
                    period_start_date,
                    period_end_date,
                ),
            )
            return self._period_open_document(
                legal_entity_reference,
                period_code,
                stored_start_date,
                stored_end_date,
                replayed=replayed,
            )

'''
    _replace_definition(
        "src/accounting_information_platform/persistence.py",
        r"^    def open_fiscal_period\(.*?(?=^    def load_fiscal_period\()",
        replacement,
    )

    path = "src/accounting_information_platform/persistence.py"
    old = '''    runtime_binding_migration_path = migration_path.parent / "0007_runtime_tenant_binding.sql"\n    if not runtime_binding_migration_path.is_file():\n        raise AccountingValidationError(\n            f"Runtime-tenant binding migration is missing at {runtime_binding_migration_path}. "\n            "Restore database/migrations/0007_runtime_tenant_binding.sql, then retry."\n        )\n'''
    new = old + '''    period_open_command_migration_path = (\n        migration_path.parent / "0008_fiscal_period_open_command.sql"\n    )\n    if not period_open_command_migration_path.is_file():\n        raise AccountingValidationError(\n            f"Fiscal-period-open command migration is missing at {period_open_command_migration_path}. "\n            "Restore database/migrations/0008_fiscal_period_open_command.sql, then retry."\n        )\n'''
    _replace_once(path, old, new)
    _replace_once(
        path,
        '            connection.execute(runtime_binding_migration_path.read_text(encoding="utf-8"))\n',
        '            connection.execute(runtime_binding_migration_path.read_text(encoding="utf-8"))\n'
        '            connection.execute(period_open_command_migration_path.read_text(encoding="utf-8"))\n',
    )


def _repair_test_fixture() -> None:
    replacement = '''    def _period_open_payload(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-09",
            "period_start_date": "2026-09-01",
            "period_end_date": "2026-09-30",
        }
        values.update(overrides)
        period_reference = str(values["fiscal_period_reference"])
        period_token = period_reference.rsplit(":", 1)[-1].replace("-", "")
        values.setdefault("idempotency_key", f"period-open:{period_reference}")
        values.setdefault(
            "source_payload_hash",
            "sha256:" + (period_token * 64)[:64],
        )
        return values

'''
    _replace_definition(
        "tests/test_postgres_posting.py",
        r"^    def _period_open_payload\(.*?(?=^    def )",
        replacement,
    )


def _repair_period_open_tests() -> None:
    path = "tests/test_period_open_command_idempotency.py"
    text = _read(path)
    text = text.replace(
        "from accounting_information_platform import AccountingValidationError, IdempotencyConflictError\n",
        "from accounting_information_platform import AccountingValidationError, IdempotencyConflictError\n"
        "from accounting_information_platform.persistence import PostgresPostingLedger\n",
        1,
    )
    marker = "\n\nclass PostgresPeriodOpenCommandIdempotencyTests(unittest.TestCase):\n"
    boundary_class = '''\n\nclass PeriodOpenPersistenceBoundaryTests(unittest.TestCase):
    """Keep direct adapter calls on the same immutable command boundary."""

    def test_direct_adapter_rejects_empty_command_key_before_database_work(self) -> None:
        """The durable adapter rejects an empty period-open command key before sessions."""
        ledger = PostgresPostingLedger(
            "postgresql://unused.example.invalid/accounting",
            "urn:cwl:tenant_period_open_command",
        )
        with mock.patch.object(ledger, "_session") as session:
            with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
                ledger.open_fiscal_period(
                    "urn:cwl:legal_entity:period_open_command",
                    "2026-09",
                    idempotency_key=" ",
                    source_payload_hash="sha256:" + "a" * 64,
                )
            session.assert_not_called()

    def test_direct_adapter_rejects_invalid_source_hash_before_database_work(self) -> None:
        """The durable adapter rejects malformed source evidence before sessions."""
        ledger = PostgresPostingLedger(
            "postgresql://unused.example.invalid/accounting",
            "urn:cwl:tenant_period_open_command",
        )
        with mock.patch.object(ledger, "_session") as session:
            with self.assertRaisesRegex(AccountingValidationError, "source_payload_hash"):
                ledger.open_fiscal_period(
                    "urn:cwl:legal_entity:period_open_command",
                    "2026-09",
                    idempotency_key="period-open-command-v1",
                    source_payload_hash="sha256:not-a-digest",
                )
            session.assert_not_called()
'''
    if marker not in text:
        raise RuntimeError("period-open PostgreSQL test class marker not found")
    text = text.replace(marker, boundary_class + marker, 1)

    old_tail = '''        with self.assertRaisesRegex(IdempotencyConflictError, "different payload"):\n            accept_period_open(\n                {**payload, "source_payload_hash": "sha256:" + "c" * 64},\n                posting.DATABASE_URL,\n                self.case.policy.tenant_reference,\n            )\n'''
    new_tail = old_tail + '''\n        with self.assertRaisesRegex(IdempotencyConflictError, "different payload"):\n            accept_period_open(\n                {**payload, "period_end_date": "2026-10-01"},\n                posting.DATABASE_URL,\n                self.case.policy.tenant_reference,\n            )\n\n        second_key = accept_period_open(\n            {**payload, "idempotency_key": "period-open-command-v2"},\n            posting.DATABASE_URL,\n            self.case.policy.tenant_reference,\n        )\n        self.assertTrue(bool(second_key["replayed"]))\n\n        with self.assertRaisesRegex(AccountingValidationError, "dates do not match"):\n            accept_period_open(\n                {\n                    **payload,\n                    "idempotency_key": "period-open-command-v3",\n                    "period_end_date": "2026-10-01",\n                    "source_payload_hash": "sha256:" + "d" * 64,\n                },\n                posting.DATABASE_URL,\n                self.case.policy.tenant_reference,\n            )\n'''
    if old_tail not in text:
        raise RuntimeError("period-open conflict test marker not found")
    _write(path, text.replace(old_tail, new_tail, 1))


def _repair_repository_contract() -> None:
    _replace_once(
        "scripts/validate_repository.py",
        '    "database/migrations/0007_runtime_tenant_binding.sql",\n',
        '    "database/migrations/0007_runtime_tenant_binding.sql",\n'
        '    "database/migrations/0008_fiscal_period_open_command.sql",\n',
    )


def _repair_docs() -> None:
    _replace_once(
        "docs/adr/0015-http-fiscal-period-open.md",
        "Open inserts the existing `fiscal_period` row shape (`period_code`, `period_start_date`, `period_end_date`, `period_status_code=open`) on the tenant calendar, or replays an already-open period without a second row. A `hard_closed` or `soft_closed` period is not reopened. GET returns the persisted status and dates. Missing catalog facts are not invented. A tenant-header mismatch is rejected before a write.",
        "Open requires a tenant-scoped `idempotency_key` and canonical immutable `source_payload_hash`. The command records durable `fiscal_period_open_command` evidence atomically with a newly inserted `fiscal_period` row, or records the command against an already-open matching period. The same tenant/key/payload replays the recorded result even after the period later closes; reuse of the key with changed entity, period, requested dates, or source hash fails closed. A new command cannot reopen a `hard_closed` or `soft_closed` period. GET returns the persisted status and dates. Missing catalog facts are not invented. A tenant-header mismatch is rejected before a write.",
    )
    _replace_once(
        "docs/adr/0015-http-fiscal-period-open.md",
        "Controllers can open the next fiscal period and then post or close without SQL. Chart, journal, and close authority stay on the existing tables. Cross-tenant open and period reads write zero rows.",
        "Controllers can open the next fiscal period and then post or close without SQL. Durable command evidence makes retries auditable without treating the mutable period status as the command identity. Chart, journal, and close authority stay on the existing tables. Cross-tenant open and period reads write zero rows.",
    )
    _replace_once(
        "docs/DATA_MODEL.md",
        "- `journal_proposal_record`: immutable external proposal identity, contract version, tenant-scoped idempotency key, and source-payload hash.\n",
        "- `journal_proposal_record`: immutable external proposal identity, contract version, tenant-scoped idempotency key, and source-payload hash.\n"
        "- `fiscal_period_open_command`: append-only period-open command evidence binding tenant, legal entity, fiscal period, tenant-scoped idempotency key, canonical source-payload hash, and requested dates. Exact retries replay this evidence; changed payload under the same key conflicts.\n",
    )
    _replace_once(
        "docs/ERD.md",
        "    fiscal_calendar ||--o{ fiscal_period : contains\n",
        "    fiscal_calendar ||--o{ fiscal_period : contains\n"
        "    tenant_account ||--o{ fiscal_period_open_command : scopes\n"
        "    legal_entity_record ||--o{ fiscal_period_open_command : authorizes\n"
        "    fiscal_period ||--o{ fiscal_period_open_command : opens\n",
    )
    _replace_once(
        "docs/ERD.md",
        "`journal_proposal_record` preserves the command-side idempotency and immutable source-payload hash that precede posting. `journal_source_reference` preserves source lineage attached to a journal. `posting_receipt` is the authoritative source-facing outcome, and `outbox_event` is committed transactionally with the accounting state it publishes.",
        "`journal_proposal_record` preserves the command-side idempotency and immutable source-payload hash that precede posting. `fiscal_period_open_command` separately preserves period-open command identity, source hash, requested dates, and the period/legal-entity foreign keys so status changes do not erase replay evidence. `journal_source_reference` preserves source lineage attached to a journal. `posting_receipt` is the authoritative source-facing outcome, and `outbox_event` is committed transactionally with the accounting state it publishes.",
    )
    _replace_once(
        "docs/OPERABILITY.md",
        "Apply migrations in numeric order through `0007_runtime_tenant_binding.sql` before starting the service.",
        "Apply migrations in numeric order through `0008_fiscal_period_open_command.sql` before starting the service.",
    )
    _replace_once(
        "docs/OPERABILITY.md",
        "database/migrations/0007_runtime_tenant_binding.sql\n",
        "database/migrations/0007_runtime_tenant_binding.sql\n"
        "database/migrations/0008_fiscal_period_open_command.sql\n",
    )
    _replace_once(
        "docs/OPERABILITY.md",
        "Migration `0007_runtime_tenant_binding.sql` replaces caller-selected tenant authority with owner-controlled runtime-login binding and must be installed before runtime database privileges are treated as production-ready.",
        "Migration `0007_runtime_tenant_binding.sql` replaces caller-selected tenant authority with owner-controlled runtime-login binding. Migration `0008_fiscal_period_open_command.sql` adds forced-RLS, append-only command evidence so fiscal-period-open retries are bound to the original tenant key and source hash. Both must be installed before runtime database privileges are treated as production-ready.",
    )
    _replace_once(
        "docs/OPERABILITY.md",
        "For a normal proposal retry, reuse the original tenant-scoped idempotency key only when the immutable payload evidence is identical. Changed evidence under the same key is a conflict and requires correction at the source, not a new journal under the old key.\n",
        "For a normal proposal retry, reuse the original tenant-scoped idempotency key only when the immutable payload evidence is identical. Changed evidence under the same key is a conflict and requires correction at the source, not a new journal under the old key.\n\nFor a fiscal-period-open retry, reuse the original `idempotency_key` and `source_payload_hash`. Exact replay returns the recorded open result even if that period has subsequently closed; changed scope, dates, or source hash under the same key is an idempotency conflict. A different command key may acknowledge an already-open matching period, but it cannot reopen a soft- or hard-closed period.\n",
    )
    _replace_once(
        "README.md",
        "`database/migrations/0001_accounting_foundation.sql`). Persistence is still\nlocal to this repository; it is not a Naruon or sibling checkout.",
        "the checked-in migration chain through `database/migrations/0008_fiscal_period_open_command.sql`). Persistence is still\nlocal to this repository; it is not a Naruon or sibling checkout.",
    )
    _replace_once(
        "CHANGELOG.md",
        "### Fixed\n\n",
        "### Fixed\n\n- Fiscal-period-open commands now require a tenant-scoped idempotency key and canonical source-payload hash, persist append-only command evidence under forced RLS, replay only the exact command, and reject changed scope/date/hash under the same key. In-memory reversal retry now consults an already-retained immutable reversal journal before applying current-period admission, so cache loss cannot turn a historical exact replay into a new closed-period write attempt.\n",
    )


def main() -> None:
    """Apply only the two causal repairs proven RED on the exact parent head."""
    migration_path = ROOT / "database/migrations/0008_fiscal_period_open_command.sql"
    if migration_path.exists():
        raise RuntimeError("0008 fiscal-period-open command migration already exists")
    _write("database/migrations/0008_fiscal_period_open_command.sql", _migration())
    _repair_accept()
    _repair_reversal_order()
    _repair_persistence()
    _repair_test_fixture()
    _repair_period_open_tests()
    _repair_repository_contract()
    _repair_docs()


if __name__ == "__main__":
    main()
