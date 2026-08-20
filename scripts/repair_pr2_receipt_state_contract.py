"""Enforce state-specific posting-receipt evidence in schemas and PostgreSQL."""

from __future__ import annotations

import json
from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def strengthen_json_schema() -> None:
    """Require and exclude evidence fields according to the authoritative outcome state."""
    path = Path("schemas/accounting-posting-receipt.schema.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["allOf"] = [
        {
            "if": {"properties": {"posting_status_code": {"const": "posted"}}, "required": ["posting_status_code"]},
            "then": {
                "required": ["journal_reference", "posted_at", "line_count"],
                "properties": {"line_count": {"minimum": 1}},
                "not": {"anyOf": [
                    {"required": ["hold_reason_code"]},
                    {"required": ["rejection_reason_code"]},
                    {"required": ["reversal_of_journal_reference"]},
                ]},
            },
        },
        {
            "if": {"properties": {"posting_status_code": {"const": "reversed"}}, "required": ["posting_status_code"]},
            "then": {
                "required": [
                    "journal_reference",
                    "reversal_of_journal_reference",
                    "posted_at",
                    "line_count",
                ],
                "properties": {"line_count": {"minimum": 1}},
                "not": {"anyOf": [
                    {"required": ["hold_reason_code"]},
                    {"required": ["rejection_reason_code"]},
                ]},
            },
        },
        {
            "if": {"properties": {"posting_status_code": {"const": "held"}}, "required": ["posting_status_code"]},
            "then": {
                "required": ["hold_reason_code"],
                "not": {"anyOf": [
                    {"required": ["journal_reference"]},
                    {"required": ["reversal_of_journal_reference"]},
                    {"required": ["rejection_reason_code"]},
                    {"required": ["posted_at"]},
                ]},
            },
        },
        {
            "if": {"properties": {"posting_status_code": {"const": "rejected"}}, "required": ["posting_status_code"]},
            "then": {
                "required": ["rejection_reason_code"],
                "not": {"anyOf": [
                    {"required": ["journal_reference"]},
                    {"required": ["reversal_of_journal_reference"]},
                    {"required": ["hold_reason_code"]},
                    {"required": ["posted_at"]},
                ]},
            },
        },
    ]
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def strengthen_database_receipt() -> None:
    """Persist hold evidence and reject impossible receipt-state combinations."""
    path = "database/migrations/0001_accounting_foundation.sql"
    text = _read(path)
    old = '''    receipt_status_code text NOT NULL CHECK (receipt_status_code IN ('posted', 'held', 'rejected', 'reversed')),
    receipt_payload_hash text NOT NULL CHECK (receipt_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    rejection_reason_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
'''
    new = '''    receipt_status_code text NOT NULL CHECK (receipt_status_code IN ('posted', 'held', 'rejected', 'reversed')),
    receipt_payload_hash text NOT NULL CHECK (receipt_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    hold_reason_code text CHECK (
        hold_reason_code IS NULL OR hold_reason_code ~ '^[a-z][a-z0-9_]{1,63}$'
    ),
    rejection_reason_code text CHECK (
        rejection_reason_code IS NULL OR rejection_reason_code ~ '^[a-z][a-z0-9_]{1,63}$'
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
'''
    if new not in text:
        if old not in text:
            raise SystemExit("posting-receipt reason-column anchor drifted")
        text = text.replace(old, new, 1)

    unique_anchor = '''    UNIQUE (tenant_account_id, proposal_record_id),
    UNIQUE (tenant_account_id, posting_receipt_id)
);
'''
    contract = '''    UNIQUE (tenant_account_id, proposal_record_id),
    UNIQUE (tenant_account_id, posting_receipt_id),
    CONSTRAINT receipt_evidence_contract CHECK (
        (
            receipt_status_code IN ('posted', 'reversed')
            AND general_journal_id IS NOT NULL
            AND hold_reason_code IS NULL
            AND rejection_reason_code IS NULL
        )
        OR (
            receipt_status_code = 'held'
            AND general_journal_id IS NULL
            AND hold_reason_code IS NOT NULL
            AND rejection_reason_code IS NULL
        )
        OR (
            receipt_status_code = 'rejected'
            AND general_journal_id IS NULL
            AND hold_reason_code IS NULL
            AND rejection_reason_code IS NOT NULL
        )
    )
);
'''
    if contract not in text:
        if unique_anchor not in text:
            raise SystemExit("posting-receipt evidence constraint anchor drifted")
        text = text.replace(unique_anchor, contract, 1)
    _write(path, text)


def add_contract_tests() -> None:
    """Test JSON state rules and PostgreSQL state/reason combinations."""
    path = "tests/test_repository_contracts.py"
    text = _read(path)
    if "test_receipt_schema_requires_state_specific_evidence" not in text:
        marker = "    def test_proposal_schema_forbids_retained_earnings_role(self) -> None:\n"
        addition = '''    def test_receipt_schema_requires_state_specific_evidence(self) -> None:
        """Each receipt outcome carries only the evidence that makes that state auditable."""
        receipt = self._schema("accounting-posting-receipt.schema.json")
        branches = {
            item["if"]["properties"]["posting_status_code"]["const"]: item["then"]
            for item in receipt["allOf"]
        }
        self.assertEqual(set(branches), {"posted", "held", "rejected", "reversed"})
        self.assertTrue({"journal_reference", "posted_at", "line_count"} <= set(branches["posted"]["required"]))
        self.assertIn("hold_reason_code", branches["held"]["required"])
        self.assertIn("rejection_reason_code", branches["rejected"]["required"])
        self.assertTrue(
            {"journal_reference", "reversal_of_journal_reference", "posted_at", "line_count"}
            <= set(branches["reversed"]["required"])
        )

'''
        if marker not in text:
            raise SystemExit("receipt schema test insertion marker drifted")
        text = text.replace(marker, addition + marker, 1)
        _write(path, text)

    pg_path = "tests/test_postgres_posting.py"
    pg = _read(pg_path)
    if "test_database_rejects_receipt_state_without_required_evidence" not in pg:
        marker = "    def test_posts_balanced_two_line_journal_and_ties_trial_balance(self) -> None:\n"
        addition = '''    def test_database_rejects_receipt_state_without_required_evidence(self) -> None:
        """Held/rejected/posting receipt rows cannot omit or mix state-specific evidence."""
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            proposal_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, uuidv7(), 1, %s, %s, 'rejected', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (self.tenant_id, f"receipt-state:{uuid.uuid4()}", "sha256:" + "9" * 64),
            ).fetchone()[0]
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_integration.posting_receipt (
                        tenant_account_id, proposal_record_id, receipt_status_code,
                        receipt_payload_hash
                    )
                    VALUES (%s, %s, 'held', %s)
                    """,
                    (self.tenant_id, proposal_id, "sha256:" + "8" * 64),
                )
            connection.rollback()

        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            proposal_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, uuidv7(), 1, %s, %s, 'rejected', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (self.tenant_id, f"receipt-state:{uuid.uuid4()}", "sha256:" + "7" * 64),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_integration.posting_receipt (
                    tenant_account_id, proposal_record_id, receipt_status_code,
                    receipt_payload_hash, rejection_reason_code
                )
                VALUES (%s, %s, 'rejected', %s, 'policy_rejected')
                """,
                (self.tenant_id, proposal_id, "sha256:" + "6" * 64),
            )
            connection.commit()

'''
        if marker not in pg:
            raise SystemExit("PostgreSQL receipt-state regression marker drifted")
        _write(pg_path, pg.replace(marker, addition + marker, 1))


def update_docs() -> None:
    """Record state-specific authoritative receipt evidence in buyer and engineering docs."""
    architecture_path = "docs/ARCHITECTURE.md"
    architecture = _read(architecture_path)
    sentence = (
        "\nPosting receipts are state-evidenced facts: posted/reversed outcomes require a "
        "journal reference, while held and rejected outcomes require their purpose-specific "
        "reason and cannot masquerade as a persisted journal.\n"
    )
    if "Posting receipts are state-evidenced facts" not in architecture:
        _write(architecture_path, architecture.rstrip() + sentence)

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    line = "- Enforced state-specific posting-receipt evidence for posted, reversed, held, and rejected outcomes in JSON Schema and PostgreSQL.\n"
    if line not in changelog:
        marker = "### Changed\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG Changed anchor drifted")
        _write(changelog_path, changelog.replace(marker, marker + "\n" + line, 1))


def main() -> None:
    """Apply posting-receipt evidence contracts and regressions."""
    strengthen_json_schema()
    strengthen_database_receipt()
    add_contract_tests()
    update_docs()


if __name__ == "__main__":
    main()
