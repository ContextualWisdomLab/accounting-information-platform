"""RED contracts for canonical reconciliation-allocation currency admission.

Allocation evidence must carry the accounting core's three-uppercase-letter
currency syntax. A non-blank arbitrary string is not sufficient merely because
it can be stored beside exact Decimal money.
"""

from __future__ import annotations

from decimal import Decimal
import unittest

from accounting_information_platform.allocation import (
    ReconciliationAllocation,
    aggregate_allocations,
)


class ReconciliationAllocationCurrencyDomainRedTests(unittest.TestCase):
    """Require canonical currency syntax on direct and aggregate allocations."""

    @staticmethod
    def _allocation(*, currency_code: str) -> ReconciliationAllocation:
        """Construct otherwise-valid immutable allocation evidence."""
        return ReconciliationAllocation(
            tenant_account_reference="tenant-001",
            reconciliation_run_reference="run-001",
            statement_entry_reference="statement-001",
            journal_reference="journal-001",
            allocated_amount=Decimal("1000.00"),
            currency_code=currency_code,
        )

    def test_direct_allocation_rejects_malformed_currency_syntax(self) -> None:
        """Durable allocation evidence rejects non-canonical currency strings."""
        for currency_code in ("krw", "KRWW", "K1W", " KRW"):
            with self.subTest(currency_code=currency_code):
                with self.assertRaisesRegex(ValueError, "currency_code"):
                    self._allocation(currency_code=currency_code)

    def test_aggregate_rejects_malformed_currency_before_emitting_allocations(self) -> None:
        """Aggregate planning cannot emit rows carrying malformed currency evidence."""
        with self.assertRaisesRegex(ValueError, "currency_code"):
            aggregate_allocations(
                statement_items=(("statement-001", Decimal("1000.00")),),
                journal_total=Decimal("1000.00"),
                reconciliation_run_reference="run-001",
                tenant_account_reference="tenant-001",
                journal_reference="journal-001",
                currency_code="krw",
            )

    def test_builtin_three_uppercase_letter_currency_controls_remain_valid(self) -> None:
        """Canonical built-in currency syntax preserves existing allocation semantics."""
        direct = self._allocation(currency_code="KRW")
        self.assertEqual(direct.currency_code, "KRW")

        aggregate = aggregate_allocations(
            statement_items=(("statement-001", Decimal("1000.00")),),
            journal_total=Decimal("1000.00"),
            reconciliation_run_reference="run-001",
            tenant_account_reference="tenant-001",
            journal_reference="journal-001",
            currency_code="USD",
        )
        self.assertEqual(aggregate[0].currency_code, "USD")


if __name__ == "__main__":
    unittest.main()
