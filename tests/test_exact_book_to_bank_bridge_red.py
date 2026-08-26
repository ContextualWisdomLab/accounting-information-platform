"""RED contract for an exact, buyer-visible book-to-bank reconciliation bridge.

This slice does not post journals or approve reconciliation. It requires a pure
projection that proves the statement equation, the posted-book equation, and the
book-to-bank bridge with exact Decimal arithmetic while retaining source-population
provenance and an operator next action.
"""

from __future__ import annotations

import importlib
import unittest
from decimal import Decimal


class ExactBookToBankBridgeTests(unittest.TestCase):
    """Require exact bridge equations, provenance, and fail-closed buyer guidance."""

    def _bridge_api(self):
        module = importlib.import_module(
            "accounting_information_platform.reconciliation_bridge"
        )
        return module.BookToBankBridgeInput, module.compute_book_to_bank_bridge

    def _input(self, **overrides):
        BridgeInput, _ = self._bridge_api()
        values = {
            "reconciliation_run_reference": "run-2026-08-001",
            "statement_population_reference": "statement-population-001",
            "book_population_reference": "posted-cash-population-001",
            "currency_code": "KRW",
            "statement_opening_balance": Decimal("1000.00"),
            "statement_period_movements": Decimal("250.00"),
            "statement_closing_balance": Decimal("1250.00"),
            "book_opening_balance": Decimal("900.00"),
            "posted_cash_book_movements": Decimal("300.00"),
            "book_closing_balance": Decimal("1200.00"),
            "reconciled_book_balance": Decimal("1200.00"),
            "outstanding_book_items": Decimal("100.00"),
            "outstanding_bank_items": Decimal("50.00"),
        }
        values.update(overrides)
        return BridgeInput(**values)

    def test_exact_bridge_ties_and_preserves_population_provenance(self) -> None:
        """A tying bridge exposes exact values and immutable source populations."""
        _, compute_bridge = self._bridge_api()

        result = compute_bridge(self._input())

        self.assertEqual(result.status_code, "reconciled")
        self.assertEqual(result.unexplained_difference, Decimal("0.00"))
        self.assertEqual(result.statement_closing_balance, Decimal("1250.00"))
        self.assertEqual(result.book_closing_balance, Decimal("1200.00"))
        self.assertEqual(result.reconciliation_run_reference, "run-2026-08-001")
        self.assertEqual(
            result.statement_population_reference, "statement-population-001"
        )
        self.assertEqual(result.book_population_reference, "posted-cash-population-001")
        self.assertIn("period-close evidence", result.next_action.lower())

    def test_one_minor_unit_bridge_difference_never_rounds_to_reconciled(self) -> None:
        """A one-minor-unit difference remains explicit rather than tolerance-rounded."""
        _, compute_bridge = self._bridge_api()

        result = compute_bridge(
            self._input(outstanding_bank_items=Decimal("50.01"))
        )

        self.assertEqual(result.status_code, "not_reconciled")
        self.assertEqual(result.exception_code, "bridge_difference")
        self.assertEqual(result.unexplained_difference, Decimal("-0.01"))
        self.assertIn("0.01", result.next_action)
        self.assertIn("review", result.next_action.lower())

    def test_statement_and_book_equations_fail_closed_before_success(self) -> None:
        """Internally inconsistent source totals cannot produce success-shaped evidence."""
        _, compute_bridge = self._bridge_api()

        statement_result = compute_bridge(
            self._input(statement_closing_balance=Decimal("1249.99"))
        )
        self.assertEqual(statement_result.status_code, "not_reconciled")
        self.assertEqual(statement_result.exception_code, "statement_balance_mismatch")
        self.assertIn("statement", statement_result.next_action.lower())

        book_result = compute_bridge(
            self._input(book_closing_balance=Decimal("1199.99"))
        )
        self.assertEqual(book_result.status_code, "not_reconciled")
        self.assertEqual(book_result.exception_code, "book_balance_mismatch")
        self.assertIn("book", book_result.next_action.lower())


if __name__ == "__main__":
    unittest.main()
