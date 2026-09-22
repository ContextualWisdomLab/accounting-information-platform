"""RED contracts for canonical reconciliation-allocation currency admission.

Direct allocation evidence must carry the accounting core's three-uppercase-letter
currency syntax. Aggregate planning no longer accepts an independent currency
scalar; its currency is inherited from admitted ``BookJournalEvidence`` so one
source-evidence boundary owns journal currency semantics.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest

from accounting_information_platform.allocation import (
    ReconciliationAllocation,
    aggregate_allocations,
)
from accounting_information_platform.reconciliation import BookJournalEvidence


class _ExplodingCurrency(str):
    """Expose caller-controlled whitespace behavior if source currency admits subclasses."""

    def strip(self, chars: str | None = None) -> str:
        """Fail if currency admission executes caller-owned string behavior."""
        raise RuntimeError("caller-controlled allocation currency must not execute")


class ReconciliationAllocationCurrencyDomainRedTests(unittest.TestCase):
    """Require canonical currency syntax on durable and aggregate evidence paths."""

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

    @staticmethod
    def _journal(*, currency_code: str = "USD") -> BookJournalEvidence:
        """Construct one admitted journal source for aggregate planning."""
        return BookJournalEvidence(
            journal_reference="journal-001",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code=currency_code,
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    def test_direct_allocation_rejects_malformed_currency_syntax(self) -> None:
        """Durable allocation evidence rejects non-canonical currency strings."""
        for currency_code in ("krw", "KRWW", "K1W", " KRW"):
            with self.subTest(currency_code=currency_code):
                with self.assertRaisesRegex(ValueError, "currency_code"):
                    self._allocation(currency_code=currency_code)

    def test_aggregate_book_currency_is_admitted_by_journal_evidence(self) -> None:
        """Malformed journal currency fails before it can become aggregate evidence."""
        with self.assertRaisesRegex(ValueError, "currency_code"):
            self._journal(currency_code="krw")

    def test_journal_currency_subclass_fails_before_caller_behavior(self) -> None:
        """Aggregate source currency rejects subclasses before whitespace behavior runs."""
        with self.assertRaisesRegex(ValueError, "currency_code"):
            self._journal(currency_code=_ExplodingCurrency("KRW"))

    def test_builtin_three_uppercase_letter_currency_controls_remain_valid(self) -> None:
        """Canonical built-in currency syntax preserves existing allocation semantics."""
        direct = self._allocation(currency_code="KRW")
        self.assertEqual(direct.currency_code, "KRW")

        aggregate = aggregate_allocations(
            statement_items=(("statement-001", Decimal("1000.00")),),
            journal_evidence=self._journal(currency_code="USD"),
            reconciliation_run_reference="run-001",
            tenant_account_reference="tenant-001",
        )
        self.assertEqual(aggregate[0].currency_code, "USD")


if __name__ == "__main__":
    unittest.main()
