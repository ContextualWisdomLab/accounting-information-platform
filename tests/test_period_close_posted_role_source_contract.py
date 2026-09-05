"""Static contract for period-close historical role classification."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE = ROOT / "src/accounting_information_platform/persistence.py"


class PeriodClosePostedRoleSourceContractTests(unittest.TestCase):
    """Keep hard-close source semantics on immutable posted journal-line facts."""

    def test_closing_journal_classifies_income_from_posted_line_role(self) -> None:
        """Do not reclassify posted P&L through the mutable current role catalog."""
        source = PERSISTENCE.read_text(encoding="utf-8")
        method = source.split("    def _post_closing_journal(", 1)[1].split(
            "    def _require_retained_earnings_mapping(", 1
        )[0]

        self.assertIn("journal_entry_line.account_role_code", method)
        self.assertIn("AND journal_entry_line.account_role_code IN (", method)
        self.assertIn(
            "GROUP BY chart_account.chart_account_code,\n"
            "                     journal_entry_line.account_role_code",
            method,
        )
        self.assertNotIn("JOIN accounting_core.account_role_mapping", method)
        self.assertNotIn("account_role_mapping.account_role_code", method)


if __name__ == "__main__":
    unittest.main()
