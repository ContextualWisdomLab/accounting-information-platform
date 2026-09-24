"""RED contract for context-independent aggregate allocation conservation."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal, localcontext

from accounting_information_platform.allocation import aggregate_allocations
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class AggregateDecimalContextContractTests(unittest.TestCase):
    """Require aggregate conservation to ignore ambient Decimal precision."""

    @staticmethod
    def _statement(reference: str, amount: str) -> StatementEntryEvidence:
        """Build canonical debit statement evidence with exact string money."""
        return StatementEntryEvidence(
            statement_entry_reference=reference,
            provider_reference=f"provider-{reference}",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code="KRW",
            credit_debit_code="DBIT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @staticmethod
    def _journal(amount: str) -> BookJournalEvidence:
        """Build canonical debit journal evidence with exact string money."""
        return BookJournalEvidence(
            journal_reference="journal-aggregate-large",
            provider_reference="provider-aggregate-large",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    def test_aggregate_rejects_overallocation_hidden_by_context_rounding(self) -> None:
        """Ambient precision cannot round an oversized statement population into equality."""
        statements = (
            self._statement("statement-large", "10000000000000000000000000000"),
            self._statement("statement-one", "1"),
        )
        journal = self._journal("10000000000000000000000000000")

        with localcontext() as context:
            context.prec = 28
            with self.assertRaisesRegex(ValueError, "must equal"):
                aggregate_allocations(
                    statement_items=statements,
                    reconciliation_run_reference="run-aggregate-large",
                    tenant_account_reference="tenant-aggregate-large",
                    journal_evidence=journal,
                )

    def test_aggregate_accepts_exact_large_conservation_under_low_precision(self) -> None:
        """A truly conserved large aggregate remains valid under a tiny ambient context."""
        statements = (
            self._statement("statement-large", "10000000000000000000000000000"),
            self._statement("statement-one", "1"),
        )
        journal = self._journal("10000000000000000000000000001")

        with localcontext() as context:
            context.prec = 2
            allocations = aggregate_allocations(
                statement_items=statements,
                reconciliation_run_reference="run-aggregate-large",
                tenant_account_reference="tenant-aggregate-large",
                journal_evidence=journal,
            )

        self.assertEqual(
            tuple(allocation.allocated_amount for allocation in allocations),
            (
                Decimal("10000000000000000000000000000"),
                Decimal("1"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
