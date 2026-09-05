"""Static contracts for historical chart-account identity during period close."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE = ROOT / "src/accounting_information_platform/persistence.py"


class PeriodClosePostedAccountIdentitySourceContractTests(unittest.TestCase):
    """Separate historical close offsets from current-catalog ordinary posting."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read the production adapter once for focused source-boundary assertions."""
        cls.source = PERSISTENCE.read_text(encoding="utf-8")

    def test_ordinary_journal_insert_still_requires_current_chart_account(self) -> None:
        """Fixing close must not let ordinary new postings target expired accounts."""
        method = self.source.split("    def _insert_journal(", 1)[1].split(
            "    def _insert_receipt(", 1
        )[0]

        self.assertIn("AND valid_to IS NULL", method)

    def test_closing_source_carries_exact_posted_chart_account_identity(self) -> None:
        """Closing offsets must not reconstruct a historical account from today's code catalog."""
        method = self.source.split("    def _post_closing_journal(", 1)[1].split(
            "    def _require_retained_earnings_mapping(", 1
        )[0]

        self.assertIn("journal_entry_line.chart_account_id", method)
        self.assertIn("GROUP BY journal_entry_line.chart_account_id", method)

    def test_hard_close_does_not_require_buyer_reporting_projection(self) -> None:
        """Reporting catalog completeness must not become a second hard-close authority."""
        method = self.source.split("    def close_fiscal_period(", 1)[1].split(
            "    def open_fiscal_period(", 1
        )[0]

        self.assertNotIn("_assemble_period_close_package(", method)
        self.assertIn("_persist_period_close(", method)


if __name__ == "__main__":
    unittest.main()
