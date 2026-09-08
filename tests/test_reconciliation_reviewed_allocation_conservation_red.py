"""RED regressions for exact reviewed-allocation conservation at close boundaries."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)
import accounting_information_platform.reconciliation_close_package as close_package
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationAllocationEvidence,
    ReconciliationCloseReviewInput,
    ReconciliationCloseReviewProjection,
    ReconciliationCloseReviewScope,
    ReconciliationReviewedMatch,
    _RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
    build_reconciliation_close_review,
)


class ReconciliationReviewedAllocationConservationTests(unittest.TestCase):
    """Reject caller-shaped reviewed evidence whose allocations are not authoritative."""

    @staticmethod
    def _reviewed_match() -> ReconciliationReviewedMatch:
        return ReconciliationReviewedMatch(
            reconciliation_match_reference="match-001",
            candidate_reference="candidate-001",
            candidate_statement_reference="statement-001",
            candidate_journal_reference="journal-001",
            statement_amount=Decimal("100.00"),
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="statement-allocation-001",
                    source_reference="statement-001",
                    allocated_amount=Decimal("100.00"),
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="journal-allocation-001",
                    source_reference="journal-001",
                    allocated_amount=Decimal("1.00"),
                ),
            ),
        )

    @staticmethod
    def _multi_source_match_without_capacity_evidence() -> ReconciliationReviewedMatch:
        """Return balanced split evidence whose non-anchor source capacity is unbound."""
        return ReconciliationReviewedMatch(
            reconciliation_match_reference="match-split-001",
            candidate_reference="candidate-statement-a-journal-x",
            candidate_statement_reference="statement-a",
            candidate_journal_reference="journal-x",
            statement_amount=Decimal("60.00"),
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="statement-allocation-a",
                    source_reference="statement-a",
                    allocated_amount=Decimal("60.00"),
                ),
                ReconciliationAllocationEvidence(
                    allocation_reference="statement-allocation-b",
                    source_reference="statement-b",
                    allocated_amount=Decimal("40.00"),
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="journal-allocation-x",
                    source_reference="journal-x",
                    allocated_amount=Decimal("100.00"),
                ),
            ),
        )

    @classmethod
    def _multi_source_match_with_capacity_evidence(cls) -> ReconciliationReviewedMatch:
        """Return the same split with database-owned source capacities attached."""
        unbound = cls._multi_source_match_without_capacity_evidence()
        return replace(
            unbound,
            statement_allocations=(
                replace(
                    unbound.statement_allocations[0],
                    source_capacity=Decimal("60.00"),
                ),
                replace(
                    unbound.statement_allocations[1],
                    source_capacity=Decimal("40.00"),
                ),
            ),
            journal_allocations=(
                replace(
                    unbound.journal_allocations[0],
                    source_capacity=Decimal("100.00"),
                ),
            ),
        )

    @staticmethod
    def _decision() -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-001",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-001",),
            allocated_amount=Decimal("100.00"),
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal.",
            reconciliation_match_reference="match-001",
        )

    @staticmethod
    def _multi_source_decisions() -> tuple[ReconciliationDecision, ...]:
        return (
            ReconciliationDecision(
                statement_entry_reference="statement-a",
                decision_code="match",
                rule_code="provider_reference",
                matched_journal_references=("journal-x",),
                allocated_amount=Decimal("60.00"),
                exception_code=None,
                next_action="Review the deterministic proposal; do not post a journal.",
                reconciliation_match_reference="match-split-001",
                contract_version="reconciliation-decision/v2",
            ),
            ReconciliationDecision(
                statement_entry_reference="statement-b",
                decision_code="match",
                rule_code="provider_reference",
                matched_journal_references=("journal-x",),
                allocated_amount=Decimal("40.00"),
                exception_code=None,
                next_action="Review the deterministic proposal; do not post a journal.",
                reconciliation_match_reference="match-split-001",
                contract_version="reconciliation-decision/v2",
            ),
        )

    @staticmethod
    def _bridge():
        return compute_book_to_bank_bridge(
            BookToBankBridgeInput(
                reconciliation_run_reference="run-001",
                statement_population_reference="statement-population-001",
                book_population_reference="book-population-001",
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
                tenant_account_reference="tenant-001",
                legal_entity_reference="entity-001",
                accounting_book_reference="book-001",
                bank_account_assignment_reference="bank-assignment-001",
            )
        )

    @staticmethod
    def _scope() -> ReconciliationCloseReviewScope:
        return ReconciliationCloseReviewScope(
            tenant_account_reference="tenant-001",
            legal_entity_reference="entity-001",
            accounting_book_reference="book-001",
            bank_account_assignment_reference="bank-assignment-001",
            currency_code="KRW",
        )

    def test_close_review_rejects_unequal_statement_and_journal_allocation_totals(self) -> None:
        """Projection construction must prove exact two-sided allocation conservation."""
        with self.assertRaisesRegex(ValueError, "allocation totals must match exactly"):
            build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(),
                    decisions=(self._decision(),),
                    expected_statement_entry_references=("statement-001",),
                    reviewed_matches=(self._reviewed_match(),),
                    scope=self._scope(),
                )
            )

    def test_multi_source_close_review_requires_authoritative_source_capacities(self) -> None:
        """Aggregate equality cannot substitute for every allocated source's capacity."""
        with self.assertRaisesRegex(ValueError, "source capacit"):
            build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(),
                    decisions=self._multi_source_decisions(),
                    expected_statement_entry_references=("statement-a", "statement-b"),
                    reviewed_matches=(self._multi_source_match_without_capacity_evidence(),),
                    scope=self._scope(),
                )
            )

    def test_multi_source_close_review_accepts_complete_exact_source_capacities(self) -> None:
        """Bound capacities preserve valid aggregate evidence without weakening conservation."""
        projection = build_reconciliation_close_review(
            ReconciliationCloseReviewInput(
                bridge_result=self._bridge(),
                decisions=self._multi_source_decisions(),
                expected_statement_entry_references=("statement-a", "statement-b"),
                reviewed_matches=(self._multi_source_match_with_capacity_evidence(),),
                scope=self._scope(),
            )
        )
        self.assertEqual(projection.reviewed_match_references, ("match-split-001",))
        self.assertEqual(
            projection.reviewed_match_evidence[0].statement_allocations[1].source_capacity,
            Decimal("40.00"),
        )

    def test_multi_source_close_review_rejects_per_source_overconsumption(self) -> None:
        """Equal aggregate totals cannot hide one statement source exceeding capacity."""
        bounded = self._multi_source_match_with_capacity_evidence()
        overconsumed = replace(
            bounded,
            statement_allocations=(
                bounded.statement_allocations[0],
                replace(
                    bounded.statement_allocations[1],
                    source_capacity=Decimal("39.99"),
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "source capacity"):
            build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(),
                    decisions=self._multi_source_decisions(),
                    expected_statement_entry_references=("statement-a", "statement-b"),
                    reviewed_matches=(overconsumed,),
                    scope=self._scope(),
                )
            )

    def test_close_package_independently_rejects_unequal_allocation_totals(self) -> None:
        """Caller-constructed projections cannot bypass the package evidence boundary."""
        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-001",
            legal_entity_reference="entity-001",
            accounting_book_reference="book-001",
            bank_account_assignment_reference="bank-assignment-001",
            reconciliation_run_reference="run-001",
            statement_population_reference="statement-population-001",
            book_population_reference="book-population-001",
            currency_code="KRW",
            bank_closing_balance=Decimal("100.00"),
            posted_book_cash_balance=Decimal("100.00"),
            reconciled_balance=Decimal("100.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=1,
            reviewed_match_references=("match-001",),
            exception_count=0,
            exception_statement_entry_references=(),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=_RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=(self._reviewed_match(),),
        )
        with self.assertRaisesRegex(ValueError, "allocation totals must match exactly"):
            close_package._validate_projection(projection)

    def test_close_package_requires_multi_source_capacity_evidence(self) -> None:
        """Package verification must reject balanced but capacity-unbound split evidence."""
        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-001",
            legal_entity_reference="entity-001",
            accounting_book_reference="book-001",
            bank_account_assignment_reference="bank-assignment-001",
            reconciliation_run_reference="run-001",
            statement_population_reference="statement-population-001",
            book_population_reference="book-population-001",
            currency_code="KRW",
            bank_closing_balance=Decimal("100.00"),
            posted_book_cash_balance=Decimal("100.00"),
            reconciled_balance=Decimal("100.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=1,
            reviewed_match_references=("match-split-001",),
            exception_count=0,
            exception_statement_entry_references=(),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=_RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=(self._multi_source_match_without_capacity_evidence(),),
        )
        with self.assertRaisesRegex(ValueError, "source capacit"):
            close_package._validate_projection(projection)

    def test_close_package_accepts_capacity_bound_multi_source_projection(self) -> None:
        """Package revalidation accepts the same exact per-source capacity proof."""
        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-001",
            legal_entity_reference="entity-001",
            accounting_book_reference="book-001",
            bank_account_assignment_reference="bank-assignment-001",
            reconciliation_run_reference="run-001",
            statement_population_reference="statement-population-001",
            book_population_reference="book-population-001",
            currency_code="KRW",
            bank_closing_balance=Decimal("100.00"),
            posted_book_cash_balance=Decimal("100.00"),
            reconciled_balance=Decimal("100.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=1,
            reviewed_match_references=("match-split-001",),
            exception_count=0,
            exception_statement_entry_references=(),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=_RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=(self._multi_source_match_with_capacity_evidence(),),
        )
        self.assertIs(close_package._validate_projection(projection), projection)


if __name__ == "__main__":
    unittest.main()
