"""RED contract for repository-owned aggregate statement evidence.

Aggregate allocation must consume exact ``StatementEntryEvidence`` rather than
caller-assembled identity/amount pairs. Otherwise the planner cannot prove the
statement currency or movement direction that belong to the allocated money.
The tests hold tenant/run scope, journal evidence, and exact conservation fixed
while varying only statement provenance and source semantics.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.allocation import aggregate_allocations
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class AggregateStatementEvidenceProvenanceRedTests(unittest.TestCase):
    """Require canonical statement evidence before aggregate allocation planning."""

    @staticmethod
    def _statement(
        reference: str,
        amount: str,
        *,
        currency_code: str = "KRW",
        credit_debit_code: str = "DBIT",
    ) -> StatementEntryEvidence:
        """Build canonical statement evidence with explicit money semantics."""
        return StatementEntryEvidence(
            statement_entry_reference=reference,
            provider_reference=f"provider-{reference}",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code=currency_code,
            credit_debit_code=credit_debit_code,
            booking_date=date(2026, 9, 1),
            value_date=date(2026, 9, 1),
        )

    @staticmethod
    def _journal() -> BookJournalEvidence:
        """Build the canonical KRW debit journal control."""
        return BookJournalEvidence(
            journal_reference="journal-a",
            provider_reference="provider-journal-a",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 1),
        )

    def _plan(self, statement_items: object):
        """Hold book evidence and allocation scope constant while varying statement input."""
        return aggregate_allocations(
            statement_items=statement_items,  # type: ignore[arg-type]
            journal_evidence=self._journal(),
            reconciliation_run_reference="run-1",
            tenant_account_reference="tenant-a",
        )

    def test_exact_statement_evidence_population_is_accepted(self) -> None:
        """Canonical statement evidence preserves identity, money, and currency provenance."""
        allocations = self._plan(
            (
                self._statement("stmt-001", "300.00"),
                self._statement("stmt-002", "700.00"),
            )
        )

        self.assertEqual(len(allocations), 2)
        self.assertEqual(
            {allocation.statement_entry_reference for allocation in allocations},
            {"stmt-001", "stmt-002"},
        )
        self.assertEqual(
            sum((allocation.allocated_amount for allocation in allocations), Decimal("0")),
            Decimal("1000.00"),
        )
        self.assertTrue(all(allocation.currency_code == "KRW" for allocation in allocations))

    def test_legacy_scalar_pairs_are_not_statement_evidence(self) -> None:
        """Identity/amount pairs cannot manufacture reviewable source provenance."""
        with self.assertRaisesRegex(ValueError, "StatementEntryEvidence"):
            self._plan(
                (
                    ("stmt-001", Decimal("300.00")),
                    ("stmt-002", Decimal("700.00")),
                )
            )

    def test_cross_currency_statement_population_fails_closed(self) -> None:
        """Statement money cannot be allocated into a journal of another currency."""
        with self.assertRaisesRegex(ValueError, "currency"):
            self._plan(
                (
                    self._statement("stmt-001", "300.00", currency_code="USD"),
                    self._statement("stmt-002", "700.00", currency_code="USD"),
                )
            )

    def test_opposite_direction_statement_population_fails_closed(self) -> None:
        """Credit statement evidence cannot be aggregated into a debit journal."""
        with self.assertRaisesRegex(ValueError, "direction"):
            self._plan(
                (
                    self._statement("stmt-001", "300.00", credit_debit_code="CRDT"),
                    self._statement("stmt-002", "700.00", credit_debit_code="CRDT"),
                )
            )


if __name__ == "__main__":
    unittest.main()
