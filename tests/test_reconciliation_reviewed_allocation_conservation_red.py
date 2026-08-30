"""RED regressions for exact reviewed-allocation conservation at close boundaries."""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)
import accounting_information_platform.reconciliation_close_package as close_package
import accounting_information_platform.reconciliation_read_model as read_model
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationAllocationEvidence,
    ReconciliationCloseReviewInput,
    ReconciliationCloseReviewProjection,
    ReconciliationCloseReviewScope,
    ReconciliationReviewedMatch,
)


class ReconciliationReviewedAllocationConservationTests(unittest.TestCase):
    """Reject caller-shaped reviewed evidence whose two allocation sides do not tie."""

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

    def test_close_review_rejects_unequal_statement_and_journal_allocation_totals(self) -> None:
        """Projection construction must prove exact two-sided allocation conservation."""
        with self.assertRaisesRegex(ValueError, "allocation totals must match exactly"):
            read_model.build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(),
                    decisions=(self._decision(),),
                    expected_statement_entry_references=("statement-001",),
                    reviewed_matches=(self._reviewed_match(),),
                    scope=ReconciliationCloseReviewScope(
                        tenant_account_reference="tenant-001",
                        legal_entity_reference="entity-001",
                        accounting_book_reference="book-001",
                        bank_account_assignment_reference="bank-assignment-001",
                        currency_code="KRW",
                    ),
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
            next_action=read_model._RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=(self._reviewed_match(),),
        )
        with self.assertRaisesRegex(ValueError, "allocation totals must match exactly"):
            close_package._validate_projection(projection)


if __name__ == "__main__":
    unittest.main()
