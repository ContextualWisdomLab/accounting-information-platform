"""Storage-contract regressions for HomeTax command idempotency."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_TAX_MIGRATION = ROOT / "database/migrations/0003_home_tax_submission.sql"


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


if __name__ == "__main__":
    unittest.main()
