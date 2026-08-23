"""One-shot exact-head repair for durable soft-close command evidence."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative_path: str, old: str, new: str) -> None:
    """Replace one reviewed source fragment and fail closed on source drift."""
    path = ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"reviewed fragment drifted in {relative_path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(relative_path: str, marker: str, section: str) -> None:
    """Append one documentation section exactly once."""
    path = ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + section.strip() + "\n", encoding="utf-8")


migration = ROOT / "database/migrations/0010_soft_close_command_evidence.sql"
if migration.exists():
    raise SystemExit("0010 migration already exists; inspect source drift")
migration.write_text(
    """BEGIN;

ALTER TABLE accounting_core.accounting_book_period_control
    ADD COLUMN soft_close_idempotency_key text,
    ADD COLUMN soft_close_source_payload_hash text,
    ADD COLUMN soft_close_source_journal_count integer,
    ADD CONSTRAINT soft_close_evidence_complete_check
    CHECK (
        (
            soft_close_idempotency_key IS NULL
            AND soft_close_source_payload_hash IS NULL
            AND soft_close_source_journal_count IS NULL
        )
        OR
        (
            soft_close_idempotency_key IS NOT NULL
            AND btrim(soft_close_idempotency_key) <> ''
            AND soft_close_source_payload_hash ~ '^sha256:[0-9a-f]{64}$'
            AND soft_close_source_journal_count IS NOT NULL
            AND soft_close_source_journal_count >= 0
        )
    );

CREATE UNIQUE INDEX accounting_book_period_soft_close_key_index
    ON accounting_core.accounting_book_period_control (
        tenant_account_id, soft_close_idempotency_key
    )
    WHERE soft_close_idempotency_key IS NOT NULL;

CREATE OR REPLACE FUNCTION accounting_core.guard_soft_close_evidence_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
BEGIN
    IF OLD.soft_close_idempotency_key IS NOT NULL
       AND (
            NEW.soft_close_idempotency_key IS DISTINCT FROM OLD.soft_close_idempotency_key
            OR NEW.soft_close_source_payload_hash IS DISTINCT FROM OLD.soft_close_source_payload_hash
            OR NEW.soft_close_source_journal_count IS DISTINCT FROM OLD.soft_close_source_journal_count
       )
    THEN
        RAISE EXCEPTION
            'soft-close command evidence is immutable once recorded'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER soft_close_evidence_immutable_guard
    BEFORE UPDATE OF soft_close_idempotency_key,
                     soft_close_source_payload_hash,
                     soft_close_source_journal_count
    ON accounting_core.accounting_book_period_control
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_soft_close_evidence_update();

COMMIT;
""",
    encoding="utf-8",
)

# The same normalized command identity must reach both soft-close persistence and replay.
replace_once(
    "src/accounting_information_platform/persistence.py",
    """                        snapshot_currency_code=snapshot_currency_code,\n                        legal_entity_reference=legal_entity_reference,\n                        accounting_book_reference=accounting_book_reference,\n                    )\n                if period_status_code == \"soft_closed\":\n""",
    """                        snapshot_currency_code=snapshot_currency_code,\n                        legal_entity_reference=legal_entity_reference,\n                        accounting_book_reference=accounting_book_reference,\n                        idempotency_key=close_idempotency_key,\n                    )\n                if period_status_code == \"soft_closed\":\n""",
)
replace_once(
    "src/accounting_information_platform/persistence.py",
    """                        snapshot_currency_code=snapshot_currency_code,\n                        legal_entity_reference=legal_entity_reference,\n                        accounting_book_reference=accounting_book_reference,\n                    )\n                package = self._assemble_period_close_package(\n""",
    """                        snapshot_currency_code=snapshot_currency_code,\n                        legal_entity_reference=legal_entity_reference,\n                        accounting_book_reference=accounting_book_reference,\n                        idempotency_key=close_idempotency_key,\n                    )\n                package = self._assemble_period_close_package(\n""",
)

# Replay uses the facts captured by the command transaction, never current ledger aggregates.
replace_once(
    "src/accounting_information_platform/persistence.py",
    """        snapshot_currency_code: str,\n        legal_entity_reference: str,\n        accounting_book_reference: str,\n    ) -> PeriodCloseReceipt:\n        period_closed_at = connection.execute(\n            \"\"\"\n            SELECT COALESCE(period_closed_at, clock_timestamp())\n            FROM accounting_core.accounting_book_period_control\n            WHERE tenant_account_id = %s\n              AND accounting_book_id = %s\n              AND fiscal_period_id = %s\n            \"\"\",\n            (tenant_id, book_id, period_id),\n        ).fetchone()[0]\n        _lines, source_journal_count, source_payload_hash = self._live_close_source(\n            connection,\n            tenant_id=tenant_id,\n            legal_entity_id=legal_entity_id,\n            book_id=book_id,\n            period_end_date=period_end_date,\n            period_code=period_code,\n            snapshot_currency_code=snapshot_currency_code,\n            legal_entity_reference=legal_entity_reference,\n            accounting_book_reference=accounting_book_reference,\n        )\n        return PeriodCloseReceipt(\n""",
    """        snapshot_currency_code: str,\n        legal_entity_reference: str,\n        accounting_book_reference: str,\n        idempotency_key: str,\n    ) -> PeriodCloseReceipt:\n        (\n            period_closed_at,\n            stored_idempotency_key,\n            source_journal_count,\n            source_payload_hash,\n            evidence_complete,\n        ) = connection.execute(\n            \"\"\"\n            SELECT COALESCE(period_closed_at, clock_timestamp()),\n                   soft_close_idempotency_key,\n                   soft_close_source_journal_count,\n                   soft_close_source_payload_hash,\n                   (\n                       soft_close_idempotency_key IS NOT NULL\n                       AND soft_close_source_journal_count IS NOT NULL\n                       AND soft_close_source_payload_hash IS NOT NULL\n                   )\n            FROM accounting_core.accounting_book_period_control\n            WHERE tenant_account_id = %s\n              AND accounting_book_id = %s\n              AND fiscal_period_id = %s\n            \"\"\",\n            (tenant_id, book_id, period_id),\n        ).fetchone()\n        if not evidence_complete:\n            raise AccountingValidationError(\n                f\"Fiscal period {period_code} is soft_closed without durable close-command evidence. \"\n                \"Restore the original evidence through an audited migration, then retry; \"\n                \"do not reconstruct it from later ledger state.\"\n            )\n        if stored_idempotency_key != idempotency_key:\n            raise IdempotencyConflictError(\n                \"period-close idempotency key was already used by the soft-close command\"\n            )\n        return PeriodCloseReceipt(\n""",
)

# Persistence records the command evidence atomically with the book-period transition/outbox.
replace_once(
    "src/accounting_information_platform/persistence.py",
    """        snapshot_currency_code: str,\n        legal_entity_reference: str,\n        accounting_book_reference: str,\n    ) -> PeriodCloseReceipt:\n        _lines, source_journal_count, source_payload_hash = self._live_close_source(\n""",
    """        snapshot_currency_code: str,\n        legal_entity_reference: str,\n        accounting_book_reference: str,\n        idempotency_key: str,\n    ) -> PeriodCloseReceipt:\n        _lines, source_journal_count, source_payload_hash = self._live_close_source(\n""",
)
replace_once(
    "src/accounting_information_platform/persistence.py",
    """        period_closed_at = self._set_book_period_closed(\n            connection, tenant_id, book_id, period_id, \"soft_closed\"\n        )\n        self._insert_period_close_event(\n""",
    """        period_closed_at = self._set_book_period_closed(\n            connection, tenant_id, book_id, period_id, \"soft_closed\"\n        )\n        connection.execute(\n            \"\"\"\n            UPDATE accounting_core.accounting_book_period_control\n            SET soft_close_idempotency_key = %s,\n                soft_close_source_payload_hash = %s,\n                soft_close_source_journal_count = %s\n            WHERE tenant_account_id = %s\n              AND accounting_book_id = %s\n              AND fiscal_period_id = %s\n            \"\"\",\n            (\n                idempotency_key,\n                source_payload_hash,\n                source_journal_count,\n                tenant_id,\n                book_id,\n                period_id,\n            ),\n        )\n        self._insert_period_close_event(\n""",
)

# Foundation install and repository contract must include the append-only migration.
replace_once(
    "src/accounting_information_platform/persistence.py",
    '    """Apply the checked-in PostgreSQL 18 foundation through runtime tenant binding."""',
    '    """Apply the checked-in PostgreSQL 18 accounting foundation in migration order."""',
)
replace_once(
    "src/accounting_information_platform/persistence.py",
    """    if not book_period_control_migration_path.is_file():\n        raise AccountingValidationError(\n            f\"Accounting-book-period control migration is missing at {book_period_control_migration_path}. \"\n            \"Restore database/migrations/0009_accounting_book_period_control.sql, then retry.\"\n        )\n    psycopg = _import_psycopg()\n""",
    """    if not book_period_control_migration_path.is_file():\n        raise AccountingValidationError(\n            f\"Accounting-book-period control migration is missing at {book_period_control_migration_path}. \"\n            \"Restore database/migrations/0009_accounting_book_period_control.sql, then retry.\"\n        )\n    soft_close_evidence_migration_path = (\n        migration_path.parent / \"0010_soft_close_command_evidence.sql\"\n    )\n    if not soft_close_evidence_migration_path.is_file():\n        raise AccountingValidationError(\n            f\"Soft-close command-evidence migration is missing at {soft_close_evidence_migration_path}. \"\n            \"Restore database/migrations/0010_soft_close_command_evidence.sql, then retry.\"\n        )\n    psycopg = _import_psycopg()\n""",
)
replace_once(
    "src/accounting_information_platform/persistence.py",
    """            connection.execute(period_open_command_migration_path.read_text(encoding=\"utf-8\"))\n            connection.execute(book_period_control_migration_path.read_text(encoding=\"utf-8\"))\n""",
    """            connection.execute(period_open_command_migration_path.read_text(encoding=\"utf-8\"))\n            connection.execute(book_period_control_migration_path.read_text(encoding=\"utf-8\"))\n            connection.execute(soft_close_evidence_migration_path.read_text(encoding=\"utf-8\"))\n""",
)
replace_once(
    "scripts/validate_repository.py",
    '    "database/migrations/0009_accounting_book_period_control.sql",\n',
    '    "database/migrations/0009_accounting_book_period_control.sql",\n    "database/migrations/0010_soft_close_command_evidence.sql",\n',
)

# Explicitly cover the new migration-loader branch and install-order contract.
replace_once(
    "tests/test_foundation_install_manifest_contract.py",
    """import unittest\nfrom pathlib import Path\n\nfrom scripts.validate_repository import REQUIRED_FILES\n""",
    """import unittest\nfrom pathlib import Path\nfrom unittest.mock import patch\n\nfrom accounting_information_platform import AccountingValidationError, apply_foundation_migration\nfrom scripts.validate_repository import REQUIRED_FILES\n""",
)
replace_once(
    "tests/test_foundation_install_manifest_contract.py",
    """                self.assertLess(text.index(migration_six), text.index(migration_seven))\n\n\nif __name__ == \"__main__\":\n""",
    """                self.assertLess(text.index(migration_six), text.index(migration_seven))\n\n    def test_required_files_and_install_docs_include_soft_close_command_evidence(self) -> None:\n        \"\"\"Soft-close evidence migration follows book-period control in operator docs.\"\"\"\n        migration_nine = \"database/migrations/0009_accounting_book_period_control.sql\"\n        migration_ten = \"database/migrations/0010_soft_close_command_evidence.sql\"\n        self.assertIn(migration_ten, set(REQUIRED_FILES))\n        for relative_path in (\"docs/OPERABILITY.md\", \"docs/ARCHITECTURE.md\"):\n            with self.subTest(relative_path=relative_path):\n                text = (ROOT / relative_path).read_text(encoding=\"utf-8\")\n                self.assertIn(migration_nine, text)\n                self.assertIn(migration_ten, text)\n                self.assertLess(text.index(migration_nine), text.index(migration_ten))\n\n    def test_install_fails_closed_when_soft_close_evidence_migration_is_missing(self) -> None:\n        \"\"\"The foundation loader may not silently omit migration 0010.\"\"\"\n        original_is_file = Path.is_file\n\n        def is_file(path: Path) -> bool:\n            if path.name == \"0010_soft_close_command_evidence.sql\":\n                return False\n            return original_is_file(path)\n\n        with patch.object(Path, \"is_file\", is_file):\n            with self.assertRaises(AccountingValidationError):\n                apply_foundation_migration(\n                    \"postgresql://unused\",\n                    ROOT / \"database/migrations/0001_accounting_foundation.sql\",\n                )\n\n\nif __name__ == \"__main__\":\n""",
)

# Extend the already-RED PostgreSQL test with DB immutability and legacy fail-closed evidence.
replace_once(
    "tests/test_period_close_book_scope.py",
    """    AccountingPolicy,\n    IdempotencyConflictError,\n""",
    """    AccountingPolicy,\n    AccountingValidationError,\n    IdempotencyConflictError,\n""",
)
replace_once(
    "tests/test_period_close_book_scope.py",
    """        with self.assertRaises(IdempotencyConflictError):\n            self.ledger.close_fiscal_period(\n                self.legal_entity_reference,\n                self.stat_book_reference,\n                \"2026-08\",\n                \"KRW\",\n                period_status_code=\"soft_closed\",\n                idempotency_key=f\"{original_key}:different\",\n            )\n\n\nif __name__ == \"__main__\":\n""",
    """        with self.assertRaises(IdempotencyConflictError):\n            self.ledger.close_fiscal_period(\n                self.legal_entity_reference,\n                self.stat_book_reference,\n                \"2026-08\",\n                \"KRW\",\n                period_status_code=\"soft_closed\",\n                idempotency_key=f\"{original_key}:different\",\n            )\n\n        with psycopg.connect(DATABASE_URL) as connection:\n            with self.assertRaises(psycopg.errors.CheckViolation):\n                connection.execute(\n                    \"\"\"\n                    UPDATE accounting_core.accounting_book_period_control AS period_control\n                    SET soft_close_idempotency_key = %s\n                    FROM accounting_core.accounting_book AS accounting_book,\n                         accounting_core.fiscal_period AS fiscal_period,\n                         accounting_core.tenant_account AS tenant_account\n                    WHERE period_control.tenant_account_id = tenant_account.tenant_account_id\n                      AND period_control.accounting_book_id = accounting_book.accounting_book_id\n                      AND period_control.fiscal_period_id = fiscal_period.fiscal_period_id\n                      AND tenant_account.tenant_account_code = %s\n                      AND accounting_book.book_name = %s\n                      AND fiscal_period.period_code = '2026-08'\n                    \"\"\",\n                    (f\"{original_key}:tampered\", self.tenant_reference, self.stat_book_reference),\n                )\n            connection.rollback()\n\n    def test_legacy_soft_close_without_command_evidence_fails_closed(self) -> None:\n        \"\"\"Do not manufacture replay evidence for a migrated legacy soft-close row.\"\"\"\n        with psycopg.connect(DATABASE_URL) as connection:\n            connection.execute(\n                \"\"\"\n                UPDATE accounting_core.accounting_book_period_control AS period_control\n                SET period_status_code = 'soft_closed', period_closed_at = clock_timestamp()\n                FROM accounting_core.accounting_book AS accounting_book,\n                     accounting_core.fiscal_period AS fiscal_period,\n                     accounting_core.tenant_account AS tenant_account\n                WHERE period_control.tenant_account_id = tenant_account.tenant_account_id\n                  AND period_control.accounting_book_id = accounting_book.accounting_book_id\n                  AND period_control.fiscal_period_id = fiscal_period.fiscal_period_id\n                  AND tenant_account.tenant_account_code = %s\n                  AND accounting_book.book_name = %s\n                  AND fiscal_period.period_code = '2026-08'\n                \"\"\",\n                (self.tenant_reference, self.stat_book_reference),\n            )\n        with self.assertRaises(AccountingValidationError):\n            self.ledger.close_fiscal_period(\n                self.legal_entity_reference,\n                self.stat_book_reference,\n                \"2026-08\",\n                \"KRW\",\n                period_status_code=\"soft_closed\",\n                idempotency_key=f\"{self.tenant_reference}:legacy-soft-close\",\n            )\n\n\nif __name__ == \"__main__\":\n""",
)

# Keep operator install order explicit through migration 0010.
replace_once(
    "docs/OPERABILITY.md",
    "Apply migrations in numeric order through `0008_fiscal_period_open_command.sql` before starting the service.",
    "Apply migrations in numeric order through `0010_soft_close_command_evidence.sql` before starting the service.",
)
replace_once(
    "docs/OPERABILITY.md",
    """database/migrations/0007_runtime_tenant_binding.sql\ndatabase/migrations/0008_fiscal_period_open_command.sql\n```""",
    """database/migrations/0007_runtime_tenant_binding.sql\ndatabase/migrations/0008_fiscal_period_open_command.sql\ndatabase/migrations/0009_accounting_book_period_control.sql\ndatabase/migrations/0010_soft_close_command_evidence.sql\n```""",
)
append_once(
    "docs/ARCHITECTURE.md",
    "8. `database/migrations/0008_fiscal_period_open_command.sql`",
    """8. `database/migrations/0008_fiscal_period_open_command.sql` — durable fiscal-period-open command identity and source evidence.
9. `database/migrations/0009_accounting_book_period_control.sql` — accounting-book-scoped close authority and journal guard lookup.
10. `database/migrations/0010_soft_close_command_evidence.sql` — immutable exact soft-close command identity and source count/hash.""",
)
append_once(
    "docs/adr/0006-fiscal-period-close-snapshot.md",
    "## Exact soft-close command replay",
    """## Exact soft-close command replay

Soft-close deliberately stores no trial-balance snapshot, but it is still an authoritative state-changing command. Migration `0010_soft_close_command_evidence.sql` records the original tenant-scoped soft-close idempotency key, source-journal count and canonical close-source SHA-256 on the book-period control row in the same transaction as the state transition and outbox event. Exact replay returns those stored facts and never recomputes historical evidence from later ledger state. A different key for an already soft-closed book-period is an idempotency conflict, and database trigger protection prevents rewriting evidence once recorded.

`snapshot_currency_code` remains required on soft-close because it participates in the canonical close-source digest even though no hard-close snapshot row is created. This makes the command evidence exact without representing soft-close as a trial-balance snapshot.""",
)
append_once(
    "docs/OPERABILITY.md",
    "## Soft-close command evidence recovery",
    """## Soft-close command evidence recovery

New soft-close transitions atomically retain the original idempotency key, source-journal count and canonical source hash. Replays must use the same key and return those stored facts even after authorized adjustments change the live ledger. If a legacy migrated `soft_closed` row has no command evidence, the service fails closed; restore the original evidence through an audited migration only if it can be proven. Never synthesize historical evidence from current journals.""",
)
append_once(
    "docs/ARCHITECTURE.md",
    "## Durable soft-close command evidence",
    """## Durable soft-close command evidence

`accounting_core.accounting_book_period_control` owns book-period state. Migration `0010_soft_close_command_evidence.sql` augments soft-close rows with immutable tenant-scoped command identity plus source count/hash observed when the transition committed. Soft-close event and evidence share the accounting transaction. Replay reads stored evidence; hard-close separately owns the immutable trial-balance snapshot.""",
)
append_once(
    "docs/DATA_MODEL.md",
    "## Soft-close command evidence fields",
    """## Soft-close command evidence fields

`accounting_book_period_control` carries nullable `soft_close_idempotency_key`, `soft_close_source_payload_hash` and `soft_close_source_journal_count` for migration compatibility. PostgreSQL requires them to be all absent or complete, makes non-null keys unique per tenant and prevents changes after a key is recorded. New application soft-closes always populate the complete set atomically; all-null values represent legacy rows whose original command evidence was not recoverable during migration.""",
)
append_once(
    "docs/TEST_STRATEGY.md",
    "## Soft-close command replay regression",
    """## Soft-close command replay regression

Real PostgreSQL tests prove that first soft-close and same-key replay return the same durable source hash/count, a different key conflicts, privileged direct SQL cannot mutate recorded soft-close evidence, and a legacy soft-close with absent evidence fails closed rather than manufacturing a receipt from current ledger state.""",
)
append_once(
    "CHANGELOG.md",
    "### Durable soft-close command evidence",
    """### Durable soft-close command evidence

- Persist exact soft-close idempotency identity and source hash/count per accounting book-period.
- Replay soft-close from immutable stored evidence and reject a different command key.
- Fail closed for legacy soft-close rows without provable command evidence instead of reconstructing historical evidence from later ledger state.""",
)
