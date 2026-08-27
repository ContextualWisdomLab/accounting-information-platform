"""RED contract for a self-explanatory exact book-to-bank bridge result.

A controller must be able to reconstruct every bridge equation from the returned
read model itself.  Requiring callers to retain the input object would make an
export, close package, or audit record incomplete even when the bridge tied.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)


class BookToBankBridgeComponentEvidenceTests(unittest.TestCase):
    """Require every exact equation component to remain in buyer-visible evidence."""

    def test_reconciled_result_exposes_every_exact_bridge_component(self) -> None:
        """The result alone must reproduce statement, book, and bridge equations."""
        bridge_input = BookToBankBridgeInput(
            reconciliation_run_reference="run-2026-08-001",
            statement_population_reference="statement-population-001",
            book_population_reference="posted-cash-population-001",
            currency_code="KRW",
            statement_opening_balance=Decimal("1000.00"),
            statement_period_movements=Decimal("250.00"),
            statement_closing_balance=Decimal("1250.00"),
            book_opening_balance=Decimal("900.00"),
            posted_cash_book_movements=Decimal("300.00"),
            book_closing_balance=Decimal("1200.00"),
            reconciled_book_balance=Decimal("1200.00"),
            outstanding_book_items=Decimal("100.00"),
            outstanding_bank_items=Decimal("50.00"),
        )

        result = compute_book_to_bank_bridge(bridge_input)

        self.assertEqual(result.status_code, "reconciled")
        self.assertEqual(result.statement_opening_balance, Decimal("1000.00"))
        self.assertEqual(result.statement_period_movements, Decimal("250.00"))
        self.assertEqual(result.statement_closing_balance, Decimal("1250.00"))
        self.assertEqual(result.book_opening_balance, Decimal("900.00"))
        self.assertEqual(result.posted_cash_book_movements, Decimal("300.00"))
        self.assertEqual(result.book_closing_balance, Decimal("1200.00"))
        self.assertEqual(result.reconciled_book_balance, Decimal("1200.00"))
        self.assertEqual(result.outstanding_book_items, Decimal("100.00"))
        self.assertEqual(result.outstanding_bank_items, Decimal("50.00"))
        self.assertEqual(
            result.reconciled_book_balance
            + result.outstanding_book_items
            - result.outstanding_bank_items,
            result.statement_closing_balance,
        )


if __name__ == "__main__":
    unittest.main()
