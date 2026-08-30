"""RED regressions for complete-population reviewed source capacity conservation."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

import accounting_information_platform.reconciliation_close_package as close_package
from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationAllocationEvidence,
    ReconciliationCloseReviewInput,
    ReconciliationCloseReviewProjection,
    ReconciliationCloseReviewScope,
    ReconciliationReviewedMatch,
    _RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
    build_reconciliation_close_review,
)


class ReconciliationCrossMatchCapacityTests(unittest.TestCase):
    """Require source capacities to hold across the complete reviewed population."""

    @staticmethod
    def _bridge():
        return compute_book_to_bank_bridge(
            BookToBankBridgeInput(
                reconciliation_run_reference="run-cross-match",
                statement_population_reference="statement-population-cross-match",
                book_population_reference="book-population-cross-match",
                currency_code="KRW",
                statement_opening_balance=Decimal("0.00"),
                statement_period_movements=Decimal("100.00"),
                statement_closing_balance=Decimal("100.00"),
                book_opening_balance=Decimal("0.00"),
                posted_cash_book_movements=Decimal("100.00"),
                book_closing_balance=Decimal("100.00"),
                reconciled_book_balance=Decimal("100.00"),
                outstanding_book_items=Decimal("0.00"),
                outstanding_bank_items=Decimal("0.00"),
                tenant_account_reference="tenant-cross-match",
                legal_entity_reference="entity-cross-match",
                accounting_book_reference="book-cross-match",
                bank_account_assignment_reference="bank-assignment-cross-match",
            )
        )

    @staticmethod
    def _scope() -> ReconciliationCloseReviewScope:
        return ReconciliationCloseReviewScope(
            tenant_account_reference="tenant-cross-match",
            legal_entity_reference="entity-cross-match",
            accounting_book_reference="book-cross-match",
            bank_account_assignment_reference="bank-assignment-cross-match",
            currency_code="KRW",
        )

    @staticmethod
    def _match(
        *,
        match_reference: str,
        statement_reference: str,
        amount: Decimal,
    ) -> ReconciliationReviewedMatch:
        return ReconciliationReviewedMatch(
            reconciliation_match_reference=match_reference,
            candidate_reference=f"candidate-{match_reference}",
            candidate_statement_reference=statement_reference,
            candidate_journal_reference="journal-shared",
            statement_amount=amount,
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference=f"statement-allocation-{match_reference}",
                    source_reference=statement_reference,
                    allocated_amount=amount,
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference=f"journal-allocation-{match_reference}",
                    source_reference="journal-shared",
                    allocated_amount=amount,
                ),
            ),
        )

    @staticmethod
    def _decision(
        *,
        match_reference: str,
        statement_reference: str,
        amount: Decimal,
    ) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference=statement_reference,
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-shared",),
            allocated_amount=amount,
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal.",
            reconciliation_match_reference=match_reference,
        )

    @classmethod
    def _population(
        cls,
        second_amount: Decimal,
    ) -> tuple[tuple[ReconciliationDecision, ...], tuple[ReconciliationReviewedMatch, ...]]:
        decisions = (
            cls._decision(
                match_reference="match-a",
                statement_reference="statement-a",
                amount=Decimal("60.00"),
            ),
            cls._decision(
                match_reference="match-b",
                statement_reference="statement-b",
                amount=second_amount,
            ),
        )
        matches = (
            cls._match(
                match_reference="match-a",
                statement_reference="statement-a",
                amount=Decimal("60.00"),
            ),
            cls._match(
                match_reference="match-b",
                statement_reference="statement-b",
                amount=second_amount,
            ),
        )
        return decisions, matches

    def test_close_review_rejects_cross_match_journal_overconsumption(self) -> None:
        """Two individually valid matches cannot jointly consume 110 of capacity 100."""
        decisions, matches = self._population(Decimal("50.00"))
        with self.assertRaisesRegex(ValueError, "source capacity"):
            build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(),
                    decisions=decisions,
                    expected_statement_entry_references=("statement-a", "statement-b"),
                    reviewed_matches=matches,
                    scope=self._scope(),
                )
            )

    def test_close_review_accepts_cross_match_journal_capacity_boundary(self) -> None:
        """The complete population may exactly consume, but never exceed, capacity."""
        decisions, matches = self._population(Decimal("40.00"))
        projection = build_reconciliation_close_review(
            ReconciliationCloseReviewInput(
                bridge_result=self._bridge(),
                decisions=decisions,
                expected_statement_entry_references=("statement-a", "statement-b"),
                reviewed_matches=matches,
                scope=self._scope(),
            )
        )
        self.assertEqual(projection.reviewed_match_references, ("match-a", "match-b"))

    def test_close_package_rejects_cross_match_journal_overconsumption(self) -> None:
        """Caller-constructed package projections must re-prove population capacity."""
        _, matches = self._population(Decimal("50.00"))
        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-cross-match",
            legal_entity_reference="entity-cross-match",
            accounting_book_reference="book-cross-match",
            bank_account_assignment_reference="bank-assignment-cross-match",
            reconciliation_run_reference="run-cross-match",
            statement_population_reference="statement-population-cross-match",
            book_population_reference="book-population-cross-match",
            currency_code="KRW",
            bank_closing_balance=Decimal("100.00"),
            posted_book_cash_balance=Decimal("100.00"),
            reconciled_balance=Decimal("100.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=2,
            reviewed_match_references=("match-a", "match-b"),
            exception_count=0,
            exception_statement_entry_references=(),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=_RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=matches,
        )
        with self.assertRaisesRegex(ValueError, "source capacity"):
            close_package._validate_projection(projection)

    def test_close_package_accepts_cross_match_journal_capacity_boundary(self) -> None:
        """Package revalidation preserves the exact-capacity boundary."""
        _, matches = self._population(Decimal("40.00"))
        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-cross-match",
            legal_entity_reference="entity-cross-match",
            accounting_book_reference="book-cross-match",
            bank_account_assignment_reference="bank-assignment-cross-match",
            reconciliation_run_reference="run-cross-match",
            statement_population_reference="statement-population-cross-match",
            book_population_reference="book-population-cross-match",
            currency_code="KRW",
            bank_closing_balance=Decimal("100.00"),
            posted_book_cash_balance=Decimal("100.00"),
            reconciled_balance=Decimal("100.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=2,
            reviewed_match_references=("match-a", "match-b"),
            exception_count=0,
            exception_statement_entry_references=(),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=_RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=matches,
        )
        self.assertIs(close_package._validate_projection(projection), projection)


if __name__ == "__main__":
    unittest.main()
