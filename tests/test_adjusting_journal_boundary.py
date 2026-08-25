"""Application-boundary regressions for AIS-owned adjusting journals."""

from __future__ import annotations

import unittest

from accounting_information_platform.accept import _parse_adjusting_journal_lines
from accounting_information_platform.core import AccountingValidationError


class AdjustingJournalBoundaryTests(unittest.TestCase):
    """Reject invalid monetary lines before any PostgreSQL write is attempted."""

    def test_zero_amount_line_fails_closed_at_application_boundary(self) -> None:
        """Every adjusting-journal line must carry a strictly positive exact amount."""
        lines = [
            {
                "chart_account_code": "110100",
                "debit_credit_code": "debit",
                "amount": "0",
                "currency_code": "KRW",
            },
            {
                "chart_account_code": "410100",
                "debit_credit_code": "credit",
                "amount": "0",
                "currency_code": "KRW",
            },
        ]

        with self.assertRaisesRegex(AccountingValidationError, "amount"):
            _parse_adjusting_journal_lines(lines)


if __name__ == "__main__":
    unittest.main()
