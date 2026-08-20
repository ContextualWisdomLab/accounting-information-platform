"""Storage-contract regressions for HomeTax command idempotency."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_TAX_MIGRATION = ROOT / "database/migrations/0003_home_tax_submission.sql"
ACCEPT_SOURCE = ROOT / "src/accounting_information_platform/accept.py"
PERSISTENCE_SOURCE = ROOT / "src/accounting_information_platform/persistence.py"


class HomeTaxIdempotencyContractTests(unittest.TestCase):
    """Keep rejected HomeTax command receipts replayable and conflict-detecting."""

    def test_home_tax_storage_requires_a_tenant_scoped_idempotency_key(self) -> None:
        """A retry key is non-empty and unique inside one tenant boundary."""
        migration = HOME_TAX_MIGRATION.read_text(encoding="utf-8")
        self.assertRegex(
            migration,
            re.compile(
                r"submission_idempotency_key\s+text\s+NOT\s+NULL\s+"
                r"CHECK\s*\(btrim\(submission_idempotency_key\)\s*<>\s*''\)",
                re.IGNORECASE | re.MULTILINE,
            ),
        )
        self.assertRegex(
            migration,
            re.compile(
                r"UNIQUE\s*\(\s*tenant_account_id\s*,\s*submission_idempotency_key\s*\)",
                re.IGNORECASE | re.MULTILINE,
            ),
        )

    def test_home_tax_command_threads_idempotency_key_to_persistence(self) -> None:
        """The public command must require and pass its retry identity to durable storage."""
        accept_source = ACCEPT_SOURCE.read_text(encoding="utf-8")
        command = accept_source.split("def accept_home_tax_submission(", 1)[1].split(
            "\ndef lookup_home_tax_submissions(", 1
        )[0]
        self.assertIn('payload.get("idempotency_key")', command)
        self.assertRegex(
            command,
            re.compile(r"if\s+not\s+submission_idempotency_key\s*:", re.MULTILINE),
        )
        self.assertIn(
            "submission_idempotency_key=submission_idempotency_key",
            command,
        )

    def test_home_tax_persistence_uses_key_and_payload_hash_for_replay(self) -> None:
        """Same key+payload replays; the same key with changed evidence must fail closed."""
        persistence_source = PERSISTENCE_SOURCE.read_text(encoding="utf-8")
        method = persistence_source.split("    def persist_home_tax_submission(", 1)[1].split(
            "\n    def load_home_tax_submissions(", 1
        )[0]
        self.assertIn("submission_idempotency_key: str", method)
        self.assertIn("ON CONFLICT (tenant_account_id, submission_idempotency_key) DO NOTHING", method)
        self.assertRegex(
            method,
            re.compile(
                r"SELECT[\s\S]+submission_idempotency_key[\s\S]+register_payload_hash",
                re.IGNORECASE,
            ),
        )
        self.assertIn("IdempotencyConflictError", method)


if __name__ == "__main__":
    unittest.main()
