"""RED contract for context-independent exact allocation conservation.

Python ``Decimal`` addition uses the ambient decimal context. Reconciliation
allocation conservation must not become weaker or stricter because a caller,
worker, or dependency changed that process-local precision. The values below
stay inside the platform's durable ``numeric(38, 6)`` envelope while exposing
rounding at Python's common 28-digit context.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext
import unittest

from accounting_information_platform.allocation import (
    aggregate_allocations,
    propose_split_allocations,
)
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


_LARGE_COMPONENT = Decimal("123456789012345678901234567890.123456")
_FALSE_POSITIVE_TAIL = Decimal("0.000001")
_TRUE_CONSERVING_TAIL = Decimal("9.876544")
_TARGET = Decimal("123456789012345678901234567900.000000")


class AllocationDecimalContextRedTests(unittest.TestCase):
    """Require conservation results to be independent of ambient Decimal precision."""

    @staticmethod
    def _statement(reference: str, amount: Decimal) -> StatementEntryEvidence:
        """Build canonical debit statement evidence with a controlled exact amount."""
        return StatementEntryEvidence(
            statement_entry_reference=reference,
            provider_reference=f"provider-{reference}",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=amount,
            currency_code="KRW",
            credit_debit_code="DBIT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @staticmethod
    def _journal(reference: str, amount: Decimal) -> BookJournalEvidence:
        """Build canonical debit journal evidence with a controlled exact amount."""
        return BookJournalEvidence(
            journal_reference=reference,
            provider_reference=f"provider-{reference}",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=amount,
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    def test_split_cannot_round_a_nonconserving_population_into_a_match(self) -> None:
        """A rounded Python sum cannot manufacture exact split conservation."""
        with localcontext() as proof_context:
            proof_context.prec = 50
            self.assertNotEqual(_LARGE_COMPONENT + _FALSE_POSITIVE_TAIL, _TARGET)

        with localcontext() as caller_context:
            caller_context.prec = 28
            with self.assertRaisesRegex(ValueError, "conserve"):
                propose_split_allocations(
                    statement_evidence=self._statement("statement-001", _TARGET),
                    candidate_journals=(
                        self._journal("journal-a", _LARGE_COMPONENT),
                        self._journal("journal-b", _FALSE_POSITIVE_TAIL),
                    ),
                    reconciliation_run_reference="run-001",
                    tenant_account_reference="tenant-001",
                )

    def test_aggregate_cannot_round_a_nonconserving_population_into_a_match(self) -> None:
        """A rounded Python sum cannot manufacture exact aggregate conservation."""
        with localcontext() as proof_context:
            proof_context.prec = 50
            self.assertNotEqual(_LARGE_COMPONENT + _FALSE_POSITIVE_TAIL, _TARGET)

        with localcontext() as caller_context:
            caller_context.prec = 28
            with self.assertRaisesRegex(ValueError, "agree"):
                aggregate_allocations(
                    statement_items=(
                        self._statement("statement-a", _LARGE_COMPONENT),
                        self._statement("statement-b", _FALSE_POSITIVE_TAIL),
                    ),
                    journal_evidence=self._journal("journal-001", _TARGET),
                    reconciliation_run_reference="run-001",
                    tenant_account_reference="tenant-001",
                )

    def test_true_split_conservation_survives_low_ambient_precision(self) -> None:
        """A caller's low precision cannot turn an exact split into a false mismatch."""
        with localcontext() as proof_context:
            proof_context.prec = 50
            self.assertEqual(_LARGE_COMPONENT + _TRUE_CONSERVING_TAIL, _TARGET)

        with localcontext() as caller_context:
            caller_context.prec = 6
            allocations = propose_split_allocations(
                statement_evidence=self._statement("statement-001", _TARGET),
                candidate_journals=(
                    self._journal("journal-a", _LARGE_COMPONENT),
                    self._journal("journal-b", _TRUE_CONSERVING_TAIL),
                ),
                reconciliation_run_reference="run-001",
                tenant_account_reference="tenant-001",
            )

        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations[0].allocated_amount, _LARGE_COMPONENT)
        self.assertEqual(allocations[1].allocated_amount, _TRUE_CONSERVING_TAIL)


if __name__ == "__main__":
    unittest.main()
