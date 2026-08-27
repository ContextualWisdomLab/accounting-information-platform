"""RED contract for exact split/aggregate reconciliation allocation conservation.

A bank statement entry that must reconcile against several journal candidates
(split) produces one allocation per candidate journal and the allocations must
sum exactly to the statement amount. Several statement entries that reconcile
to journal total (aggregate) produce one allocation per statement entry and
the statement-side total must equal the book-side total. Every allocation is
immutable, tenant- and run-scoped, and carries exact ``Decimal`` money. A
proposal that would consume more than the remaining amount on either side
fails closed instead of emitting partial evidence.

The reconciliation domain still never posts, reverses, or approves a journal;
it returns evidence for an operator to review (ADR 0054).
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import BookJournalEvidence


class AllocationConservationContractTests(unittest.TestCase):
    """Require exact conservation, no double consumption, and fail-closed edges."""

    def _journal(self, *, reference: str, amount: str):
        return BookJournalEvidence(
            journal_reference=reference,
            provider_reference="ref-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=None,
        )

    def test_split_conserves_statement_amount_across_journals(self) -> None:
        """One statement split across two journals sums to the exact statement amount."""
        from accounting_information_platform.allocation import (
            ReconciliationAllocation,
            propose_split_allocations,
        )

        allocations = propose_split_allocations(
            statement_entry_reference="stmt-001",
            statement_amount=Decimal("1000.00"),
            candidate_journals=(
                self._journal(reference="journal-a", amount="400.00"),
                self._journal(reference="journal-b", amount="600.00"),
            ),
            reconciliation_run_reference="run-1",
            tenant_account_reference="tenant-a",
        )
        self.assertIsInstance(allocations, tuple)
        self.assertTrue(all(isinstance(x, ReconciliationAllocation) for x in allocations))
        self.assertEqual(sum(x.allocated_amount for x in allocations), Decimal("1000.00"))
        self.assertEqual({x.journal_reference for x in allocations}, {"journal-a", "journal-b"})
        for allocation in allocations:
            self.assertEqual(allocation.tenant_account_reference, "tenant-a")
            self.assertEqual(allocation.reconciliation_run_reference, "run-1")
            self.assertEqual(allocation.statement_entry_reference, "stmt-001")
            self.assertEqual(allocation.currency_code, "KRW")

    def test_split_candidates_must_be_finite_positive_decimals(self) -> None:
        """Zero, non-canonical, or non-finite journal money is rejected before planning."""
        from accounting_information_platform.allocation import propose_split_allocations

        for amount in ("0.00", "400.005", "NaN", "Infinity"):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    propose_split_allocations(
                        statement_entry_reference="stmt-001",
                        statement_amount=Decimal("1000.00"),
                        candidate_journals=(
                            self._journal(reference="journal-a", amount=amount),
                        ),
                        reconciliation_run_reference="run-1",
                        tenant_account_reference="tenant-a",
                    )

    def test_split_fails_closed_when_total_exceeds_statement_amount(self) -> None:
        """A split allocating more than the statement amount is never returned."""
        from accounting_information_platform.allocation import propose_split_allocations

        with self.assertRaises(ValueError):
            propose_split_allocations(
                statement_entry_reference="stmt-001",
                statement_amount=Decimal("1000.00"),
                candidate_journals=(
                    self._journal(reference="journal-a", amount="450.00"),
                    self._journal(reference="journal-b", amount="600.00"),
                ),
                reconciliation_run_reference="run-1",
                tenant_account_reference="tenant-a",
            )

    def test_allocation_row_keeps_exact_money_and_identity(self) -> None:
        """An allocation cell is immutable and exact-money validated."""
        from accounting_information_platform.allocation import ReconciliationAllocation

        with self.assertRaises(ValueError):
            ReconciliationAllocation(
                tenant_account_reference="tenant-a",
                reconciliation_run_reference="run-1",
                statement_entry_reference="stmt-001",
                journal_reference="journal-a",
                allocated_amount=Decimal("NaN"),
                currency_code="KRW",
            )
        with self.assertRaises(ValueError):
            ReconciliationAllocation(
                tenant_account_reference="tenant-a",
                reconciliation_run_reference="run-1",
                statement_entry_reference="stmt-001",
                journal_reference="journal-a",
                allocated_amount=Decimal("0.00"),
                currency_code="KRW",
            )
        with self.assertRaises(ValueError):
            ReconciliationAllocation(
                tenant_account_reference="   ",
                reconciliation_run_reference="run-1",
                statement_entry_reference="stmt-001",
                journal_reference="journal-a",
                allocated_amount=Decimal("400.00"),
                currency_code="KRW",
            )

    def test_split_requires_at_least_one_candidate(self) -> None:
        """An empty candidate tuple cannot produce a split allocation."""
        from accounting_information_platform.allocation import propose_split_allocations

        with self.assertRaises(ValueError):
            propose_split_allocations(
                statement_entry_reference="stmt-001",
                statement_amount=Decimal("1000.00"),
                candidate_journals=(),
                reconciliation_run_reference="run-1",
                tenant_account_reference="tenant-a",
            )

    def test_split_rejects_mixed_currency_candidates(self) -> None:
        """A split may only plan within one currency."""
        from accounting_information_platform.allocation import propose_split_allocations

        with self.assertRaises(ValueError):
            propose_split_allocations(
                statement_entry_reference="stmt-001",
                statement_amount=Decimal("1000.00"),
                candidate_journals=(
                    self._journal(reference="journal-a", amount="400.00"),
                    self._journal(reference="journal-b", amount="600.00").__replace__(
                        currency_code="USD"
                    ),
                ),
                reconciliation_run_reference="run-1",
                tenant_account_reference="tenant-a",
            )

    def test_aggregate_requires_statement_items(self) -> None:
        """An aggregate with no statement items fails closed."""
        from accounting_information_platform.allocation import aggregate_allocations

        with self.assertRaises(ValueError):
            aggregate_allocations(
                statement_items=(),
                journal_total=Decimal("1000.00"),
                reconciliation_run_reference="run-1",
                tenant_account_reference="tenant-a",
            )

    def test_aggregate_conserves_total_on_both_sides(self) -> None:
        """An aggregate of several statements into a journal total conserves exactly."""
        from accounting_information_platform.allocation import (
            ReconciliationAllocation,
            aggregate_allocations,
        )

        statement_items = (
            ("stmt-001", Decimal("300.00")),
            ("stmt-002", Decimal("700.00")),
        )
        allocations = aggregate_allocations(
            statement_items=statement_items,
            journal_total=Decimal("1000.00"),
            reconciliation_run_reference="run-1",
            tenant_account_reference="tenant-a",
        )
        self.assertIsInstance(allocations, tuple)
        self.assertTrue(all(isinstance(x, ReconciliationAllocation) for x in allocations))
        self.assertEqual(
            sum(x.allocated_amount for x in allocations),
            Decimal("1000.00"),
        )
        self.assertEqual(
            {x.statement_entry_reference for x in allocations},
            {"stmt-001", "stmt-002"},
        )

    def test_aggregate_fails_closed_when_sides_disagree(self) -> None:
        """An aggregate whose book total differs from the statement sum never returns."""
        from accounting_information_platform.allocation import aggregate_allocations

        with self.assertRaises(ValueError):
            aggregate_allocations(
                statement_items=(
                    ("stmt-001", Decimal("300.00")),
                    ("stmt-002", Decimal("700.00")),
                ),
                journal_total=Decimal("900.00"),
                reconciliation_run_reference="run-1",
                tenant_account_reference="tenant-a",
            )


if __name__ == "__main__":
    unittest.main()