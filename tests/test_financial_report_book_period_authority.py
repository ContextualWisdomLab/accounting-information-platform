"""Regression contracts for book-scoped financial-report period authority."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "database/migrations/0020_financial_report_source_registry.sql"
)


class FinancialReportBookPeriodAuthorityTests(unittest.TestCase):
    """Prevent report runs from falling back to tenant-global fiscal-period state."""

    def test_current_period_is_foreign_keyed_to_book_period_control(self) -> None:
        """The report's current period must exist for the selected accounting book."""
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertRegex(
            migration,
            re.compile(
                r"FOREIGN KEY\s*\(tenant_account_id, accounting_book_id, fiscal_period_id\)\s*"
                r"REFERENCES accounting_core\.accounting_book_period_control\s*\(\s*"
                r"tenant_account_id\s*,\s*accounting_book_id\s*,\s*fiscal_period_id\s*\)",
                re.IGNORECASE | re.MULTILINE,
            ),
        )

    def test_comparison_period_is_also_book_scoped(self) -> None:
        """A comparison period from an unrelated book cannot enter the same report run."""
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertRegex(
            migration,
            re.compile(
                r"FOREIGN KEY\s*\(tenant_account_id, accounting_book_id, comparison_fiscal_period_id\)\s*"
                r"REFERENCES accounting_core\.accounting_book_period_control\s*\(\s*"
                r"tenant_account_id\s*,\s*accounting_book_id\s*,\s*fiscal_period_id\s*\)",
                re.IGNORECASE | re.MULTILINE,
            ),
        )

    def test_period_state_is_derived_from_book_period_control(self) -> None:
        """Run evidence records the selected book's close state, not a tenant-global fallback."""
        migration = MIGRATION.read_text(encoding="utf-8")
        binding_function = migration.split(
            "CREATE OR REPLACE FUNCTION accounting_reporting.bind_financial_report_run_scope()",
            1,
        )[1].split("CREATE TRIGGER financial_report_run_binding_guard", 1)[0]
        self.assertIn("accounting_core.accounting_book_period_control", binding_function)
        self.assertNotIn("FROM accounting_core.fiscal_period", binding_function)


if __name__ == "__main__":
    unittest.main()
