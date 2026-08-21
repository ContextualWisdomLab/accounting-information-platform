"""One-shot normalization for durable HomeTax command provenance.

This helper exists only to apply and validate the current PR repair. The
normalization workflow removes it before publishing the canonical repaired head.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def update_home_tax_migration() -> None:
    path = "database/migrations/0003_home_tax_submission.sql"
    text = _read(path)
    old = """    submission_idempotency_key text NOT NULL
        CHECK (btrim(submission_idempotency_key) <> ''),
    submission_status_code text NOT NULL CHECK (submission_status_code IN ('rejected')),
"""
    new = """    submission_idempotency_key text NOT NULL
        CHECK (btrim(submission_idempotency_key) <> ''),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_reference text NOT NULL
        CHECK (btrim(source_payload_reference) <> ''),
    submission_status_code text NOT NULL CHECK (submission_status_code IN ('rejected')),
"""
    _write(path, _replace_once(text, old, new, path))


def update_accept_boundary() -> None:
    path = "src/accounting_information_platform/accept.py"
    text = _read(path)
    old = """        submission_idempotency_key=submission_idempotency_key,
        register_document=register_document,
        rejection_reason_code=rejection_reason_code,
"""
    new = """        submission_idempotency_key=submission_idempotency_key,
        source_payload_hash=source_payload_hash,
        source_payload_reference=source_payload_reference,
        register_document=register_document,
        rejection_reason_code=rejection_reason_code,
"""
    _write(path, _replace_once(text, old, new, path))


def update_persistence_boundary() -> None:
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)
    pattern = re.compile(
        r"(?ms)^    def persist_home_tax_submission\(.*?(?=^    def load_home_tax_submissions\()"
    )
    replacement = '''    def persist_home_tax_submission(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        submission_idempotency_key: str,
        source_payload_hash: str,
        source_payload_reference: str,
        register_document: dict[str, object],
        rejection_reason_code: str,
    ) -> dict[str, object]:
        """Persist or replay one rejected HomeTax receipt with immutable command provenance."""
        if not submission_idempotency_key:
            raise AccountingValidationError(
                "submission_idempotency_key is required. "
                "Supply the original HomeTax command key, then retry the home-tax-submission."
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", source_payload_hash) is None:
            raise AccountingValidationError(
                "source_payload_hash must be a sha256 digest. "
                "Supply immutable HomeTax source evidence, then retry the home-tax-submission."
            )
        normalized_source_reference = source_payload_reference.strip()
        if not normalized_source_reference:
            raise AccountingValidationError(
                "source_payload_reference is required. "
                "Supply the immutable HomeTax source locator, then retry the home-tax-submission."
            )
        register_payload_hash = "sha256:" + hashlib.sha256(
            json.dumps(
                register_document, separators=(",", ":"), sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        raw_as_of_date = str(register_document.get("as_of_date") or "")
        as_of_date = date.fromisoformat(raw_as_of_date) if raw_as_of_date else None
        closing_amount = Decimal(str(register_document.get("closing_amount") or "0"))
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the home-tax-submission",
            )
            self._acquire_command_lock(
                connection, f"home-tax:{submission_idempotency_key}"
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the home-tax-submission",
            )[0]
            period_id, _period_status, period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the home-tax-submission",
            )
            if as_of_date is None:
                as_of_date = period_end_date
            row = connection.execute(
                """
                INSERT INTO accounting_integration.home_tax_submission (
                    tenant_account_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    submission_idempotency_key,
                    source_payload_hash,
                    source_payload_reference,
                    submission_status_code,
                    rejection_reason_code,
                    as_of_date,
                    closing_amount,
                    register_payload_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'rejected', %s, %s, %s, %s)
                ON CONFLICT (tenant_account_id, submission_idempotency_key) DO NOTHING
                RETURNING home_tax_submission_id,
                          submission_status_code,
                          rejection_reason_code,
                          as_of_date,
                          closing_amount,
                          register_payload_hash,
                          source_payload_hash,
                          source_payload_reference,
                          legal_entity_id,
                          accounting_book_id,
                          fiscal_period_id
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    submission_idempotency_key,
                    source_payload_hash,
                    normalized_source_reference,
                    rejection_reason_code,
                    as_of_date,
                    closing_amount,
                    register_payload_hash,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT home_tax_submission_id,
                           submission_status_code,
                           rejection_reason_code,
                           as_of_date,
                           closing_amount,
                           register_payload_hash,
                           source_payload_hash,
                           source_payload_reference,
                           legal_entity_id,
                           accounting_book_id,
                           fiscal_period_id
                    FROM accounting_integration.home_tax_submission
                    WHERE tenant_account_id = %s
                      AND submission_idempotency_key = %s
                    """,
                    (tenant_id, submission_idempotency_key),
                ).fetchone()
                if row is None:
                    raise AccountingValidationError(
                        "HomeTax command replay could not find its existing receipt. "
                        "Retry the command with the same idempotency key."
                    )
                if (
                    row[5] != register_payload_hash
                    or row[6] != source_payload_hash
                    or row[7] != normalized_source_reference
                    or row[8] != legal_entity_id
                    or row[9] != book_id
                    or row[10] != period_id
                ):
                    raise IdempotencyConflictError(
                        "HomeTax idempotency key was already used with different evidence or scope. "
                        "Use a new command key for the changed submission."
                    )
        receipt_register = _home_tax_register_view(register_document)
        if not receipt_register.get("as_of_date"):
            receipt_register["as_of_date"] = row[3].isoformat()
        return _home_tax_submission_document(
            home_tax_submission_id=str(row[0]),
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            book_reference=accounting_book_reference,
            period_code=period_code,
            vat_period_register=receipt_register,
            rejection_reason_code=str(row[2]),
            submission_status_code=str(row[1]),
        )

'''
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"{path}: persistence method anchor drifted")
    _write(path, updated)


def add_missing_test_provenance() -> None:
    path = "tests/test_postgres_posting.py"
    text = _read(path)
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    insertions: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "persist_home_tax_submission":
            continue
        keyword_names = {keyword.arg for keyword in node.keywords}
        if {"source_payload_hash", "source_payload_reference"} <= keyword_names:
            continue
        register_keyword = next(
            (keyword for keyword in node.keywords if keyword.arg == "register_document"),
            None,
        )
        if register_keyword is None:
            raise SystemExit("HomeTax persistence test call lacks register_document anchor")
        target_index = register_keyword.value.lineno - 1
        indent = lines[target_index][: len(lines[target_index]) - len(lines[target_index].lstrip())]
        insertion = (
            f'{indent}source_payload_hash="sha256:" + "a" * 64,\n'
            f'{indent}source_payload_reference="urn:cwl:evidence:home_tax_test:v1",\n'
        )
        insertions.append((target_index, insertion))
    for target_index, insertion in sorted(insertions, reverse=True):
        lines.insert(target_index, insertion)
    _write(path, "".join(lines))


def strengthen_postgres_replay_test() -> None:
    path = "tests/test_postgres_posting.py"
    text = _read(path)
    marker = '''        changed_register = dict(register)
        changed_register["closing_amount"] = "2501"
'''
    if marker not in text:
        raise SystemExit("HomeTax replay test anchor drifted")
    conflict_block = '''        with self.assertRaises(IdempotencyConflictError):
            self.ledger.persist_home_tax_submission(
                legal_entity_reference=self.policy.legal_entity_reference,
                accounting_book_reference=self.policy.accounting_book_reference,
                period_code="2026-08",
                submission_idempotency_key=command_key,
                source_payload_hash="sha256:" + "b" * 64,
                source_payload_reference="urn:cwl:evidence:home_tax_test:v1",
                register_document=register,
                rejection_reason_code="hometax_transport_unavailable",
            )
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.persist_home_tax_submission(
                legal_entity_reference=self.policy.legal_entity_reference,
                accounting_book_reference=self.policy.accounting_book_reference,
                period_code="2026-08",
                submission_idempotency_key=command_key,
                source_payload_hash="sha256:" + "a" * 64,
                source_payload_reference="urn:cwl:evidence:home_tax_test:changed:v1",
                register_document=register,
                rejection_reason_code="hometax_transport_unavailable",
            )

'''
    if conflict_block not in text:
        text = text.replace(marker, conflict_block + marker, 1)
    _write(path, text)


def update_docs() -> None:
    adr_path = "docs/adr/0046-http-home-tax-submission.md"
    adr = _read(adr_path)
    paragraph = (
        "Each HomeTax write command carries a tenant-scoped idempotency key, a canonical "
        "`source_payload_hash`, and an immutable `source_payload_reference`. The durable "
        "`home_tax_submission` row stores both source fields separately from the derived "
        "VAT-register hash. An exact retry replays the stored receipt; reuse of the same "
        "command key with a changed source hash, source reference, register hash, or "
        "accounting scope fails closed.\n"
    )
    if paragraph not in adr:
        marker = "\n## Consequences\n"
        if marker not in adr:
            raise SystemExit("ADR 0046 consequences anchor drifted")
        adr = adr.replace(marker, "\n" + paragraph + marker, 1)
    _write(adr_path, adr)

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    bullet = (
        "- HomeTax rejected-command receipts now retain immutable command source hash and "
        "source reference separately from the derived VAT-register hash, and exact replay "
        "fails closed on changed provenance.\n"
    )
    if bullet not in changelog:
        marker = "## [Unreleased]\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG Unreleased anchor drifted")
        changelog = changelog.replace(marker, marker + "\n" + bullet, 1)
    _write(changelog_path, changelog)


def main() -> None:
    update_home_tax_migration()
    update_accept_boundary()
    update_persistence_boundary()
    add_missing_test_provenance()
    strengthen_postgres_replay_test()
    update_docs()


if __name__ == "__main__":
    main()
