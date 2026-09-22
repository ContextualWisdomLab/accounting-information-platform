"""RED contract for context-independent split allocation conservation."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal, localcontext

from accounting_information_platform.allocation import propose_split_allocations
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class SplitDecimalContextContractTests(unittest.TestCase):
    """Require split conservation to ignore ambient Decimal precision."""

    @staticmethod
    def _statement(amount: str) -> StatementEntryEvidence:
        """Build canonical debit statement evidence with exact string money."""
        return StatementEntryEvidence(
            statement_entry_reference="stmt-large",
            provider_reference="provider-large",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code="KRW",
            credit_debit_code="DBIT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @staticmethod
    def _journal(reference: str, amount: str) -> BookJournalEvidence:
        """Build canonical debit journal evidence with exact string money."""
        return BookJournalEvidence(
            journal_reference=reference,
            provider_reference=f"provider-{reference}",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    def test_split_rejects_overallocation_hidden_by_decimal_context_rounding(self) -> None:
        """Ambient precision cannot round an overallocated candidate total into equality."""
        statement = self._statement("10000000000000000000000000000")
        candidates = (
            self._journal("journal-large", "10000000000000000000000000000"),
            self._journal("journal-one", "1"),
        )

        with localcontext() as context:
            context.prec = 28
            with self.assertRaisesRegex(ValueError, "conserve"):
                propose_split_allocations(
                    statement_evidence=statement,
                    candidate_journals=candidates,
                    reconciliation_run_reference="run-large",
                    tenant_account_reference="tenant-large",
                )

    def test_split_accepts_exact_large_conservation_under_low_precision(self) -> None:
        """A truly conserved large split remains valid even under a tiny ambient context."""
        statement = self._statement("10000000000000000000000000001")
        candidates = (
            self._journal("journal-large", "10000000000000000000000000000"),
            self._journal("journal-one", "1"),
        )

        with localcontext() as context:
            context.prec = 2
            allocations = propose_split_allocations(
                statement_evidence=statement,
                candidate_journals=candidates,
                reconciliation_run_reference="run-large",
                tenant_account_reference="tenant-large",
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
