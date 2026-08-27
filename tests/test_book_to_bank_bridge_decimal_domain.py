"""RED contract for finite exact-decimal book-to-bank bridge inputs.

Accounting bridge evidence may be negative because balances and movements can be
signed, but every monetary component must still be a finite ``Decimal``. Binary
floating point and Decimal NaN/Infinity values are not defensible accounting
amounts and must fail before bridge arithmetic or customer-facing evidence is
constructed.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)


_MONEY_FIELDS = (
    "statement_opening_balance",
    "statement_period_movements",
    "statement_closing_balance",
    "book_opening_balance",
    "posted_cash_book_movements",
    "book_closing_balance",
    "reconciled_book_balance",
    "outstanding_book_items",
    "outstanding_bank_items",
)


def _bridge_input(**overrides: object) -> BookToBankBridgeInput:
    values: dict[str, object] = {
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
    return BookToBankBridgeInput(**values)  # type: ignore[arg-type]


class BookToBankBridgeDecimalDomainTests(unittest.TestCase):
    """Require finite Decimal money without banning legitimate signed balances."""

    def test_binary_float_is_rejected_before_bridge_arithmetic(self) -> None:
        """Type hints alone must not admit binary floating-point accounting money."""
        with self.assertRaisesRegex(ValueError, "finite Decimal"):
            compute_book_to_bank_bridge(_bridge_input(statement_opening_balance=1000.0))

    def test_every_monetary_component_rejects_non_finite_decimal_values(self) -> None:
        """NaN and infinities must never become bridge or close-review evidence."""
        for field_name in _MONEY_FIELDS:
            for invalid_value in (
                Decimal("NaN"),
                Decimal("Infinity"),
                Decimal("-Infinity"),
            ):
                with self.subTest(field=field_name, value=str(invalid_value)):
                    with self.assertRaisesRegex(ValueError, "finite Decimal"):
                        compute_book_to_bank_bridge(
                            _bridge_input(**{field_name: invalid_value})
                        )

    def test_finite_negative_balances_and_movements_remain_supported(self) -> None:
        """Domain validation must not mistake signed accounting values for invalid money."""
        result = compute_book_to_bank_bridge(
            _bridge_input(
                statement_opening_balance=Decimal("-1000.00"),
                statement_period_movements=Decimal("250.00"),
                statement_closing_balance=Decimal("-750.00"),
                book_opening_balance=Decimal("-900.00"),
                posted_cash_book_movements=Decimal("200.00"),
                book_closing_balance=Decimal("-700.00"),
                reconciled_book_balance=Decimal("-700.00"),
                outstanding_book_items=Decimal("0.00"),
                outstanding_bank_items=Decimal("50.00"),
            )
        )

        self.assertEqual(result.status_code, "reconciled")
        self.assertEqual(result.unexplained_difference, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
