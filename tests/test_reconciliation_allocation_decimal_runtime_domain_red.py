"""RED contracts for exact reconciliation-allocation monetary runtime admission.

Allocation planning must not execute caller-defined ``Decimal`` subclass behavior
while deciding whether source or allocated money is finite and positive. Durable
allocation amounts stay exact repository-owned ``Decimal`` evidence. Aggregate
book-side money now arrives through admitted ``BookJournalEvidence`` rather than
an independent caller scalar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest

from accounting_information_platform.allocation import (
    ReconciliationAllocation,
    aggregate_allocations,
    propose_split_allocations,
)
from accounting_information_platform.reconciliation import BookJournalEvidence


class _ExplodingDecimal(Decimal):
    """Expose caller-controlled numeric behavior if a Decimal subclass is admitted."""

    def is_finite(self) -> bool:
        """Fail if allocation admission executes caller-owned Decimal behavior."""
        raise RuntimeError("caller-controlled allocation Decimal must not execute")


class ReconciliationAllocationDecimalRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in Decimal values at every allocation money boundary."""

    @staticmethod
    def _journal() -> BookJournalEvidence:
        """Return one otherwise-valid journal candidate for allocation planning."""
        return BookJournalEvidence(
            journal_reference="journal-001",
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="CRDT",
            accounting_date=date(2026, 9, 22),
        )

    def test_allocation_amount_rejects_decimal_subclass_before_numeric_behavior(self) -> None:
        """Direct durable allocation evidence cannot execute subclass finiteness logic."""
        with self.assertRaisesRegex(ValueError, "allocated_amount"):
            ReconciliationAllocation(
                tenant_account_reference="tenant-001",
                reconciliation_run_reference="run-001",
                statement_entry_reference="statement-001",
                journal_reference="journal-001",
                allocated_amount=_ExplodingDecimal("1000.00"),
                currency_code="KRW",
            )

    def test_split_statement_total_rejects_decimal_subclass(self) -> None:
        """Split source money fails before caller-owned Decimal behavior can run."""
        with self.assertRaisesRegex(ValueError, "statement_amount"):
            propose_split_allocations(
                statement_entry_reference="statement-001",
                statement_amount=_ExplodingDecimal("1000.00"),
                candidate_journals=(self._journal(),),
                reconciliation_run_reference="run-001",
                tenant_account_reference="tenant-001",
            )

    def test_book_side_money_rejects_decimal_subclass_before_aggregate_planning(self) -> None:
        """Aggregate book money must pass journal-evidence admission before planning."""
        with self.assertRaisesRegex(ValueError, "amount"):
            BookJournalEvidence(
                journal_reference="journal-001",
                provider_reference=None,
                end_to_end_reference=None,
                account_servicer_reference=None,
                amount=_ExplodingDecimal("1000.00"),
                currency_code="KRW",
                credit_debit_code="CRDT",
                accounting_date=date(2026, 9, 22),
            )

    def test_aggregate_statement_money_rejects_decimal_subclass(self) -> None:
        """Statement-side aggregate money retains exact built-in Decimal admission."""
        with self.assertRaisesRegex(ValueError, "statement statement-001 amount"):
            aggregate_allocations(
                statement_items=(("statement-001", _ExplodingDecimal("1000.00")),),
                journal_evidence=self._journal(),
                reconciliation_run_reference="run-001",
                tenant_account_reference="tenant-001",
            )

    def test_exact_builtin_decimal_controls_remain_valid(self) -> None:
        """Existing exact built-in Decimal allocation semantics remain unchanged."""
        split = propose_split_allocations(
            statement_entry_reference="statement-001",
            statement_amount=Decimal("1000.00"),
            candidate_journals=(self._journal(),),
            reconciliation_run_reference="run-001",
            tenant_account_reference="tenant-001",
        )
        self.assertEqual(split[0].allocated_amount, Decimal("1000.00"))

        aggregate = aggregate_allocations(
            statement_items=(("statement-001", Decimal("1000.00")),),
            journal_evidence=self._journal(),
            reconciliation_run_reference="run-001",
            tenant_account_reference="tenant-001",
        )
        self.assertEqual(aggregate[0].allocated_amount, Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
