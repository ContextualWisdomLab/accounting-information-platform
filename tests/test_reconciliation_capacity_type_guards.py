"""Fail closed when runtime callers provide non-Decimal source capacities."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import cast

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
    _validate_reviewed_match_population,
)


class ReconciliationCapacityTypeGuardTests(unittest.TestCase):
    """Reject malformed capacity types at both close-review trust boundaries."""

    @staticmethod
    def _bad_match() -> ReconciliationReviewedMatch:
        """Build one otherwise-valid match whose capacity arrived as text."""
        return ReconciliationReviewedMatch(
            reconciliation_match_reference="match-capacity-type",
            candidate_reference="candidate-capacity-type",
            candidate_statement_reference="statement-anchor",
            candidate_journal_reference="journal-anchor",
            statement_amount=Decimal("10.00"),
            journal_amount=Decimal("10.00"),
            rule_code="provider_reference",
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="statement-allocation",
                    source_reference="statement-anchor",
                    allocated_amount=Decimal("10.00"),
                    source_capacity=cast(Decimal, "10.00"),
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="journal-allocation",
                    source_reference="journal-anchor",
                    allocated_amount=Decimal("10.00"),
                ),
            ),
        )

    @staticmethod
    def _scope() -> ReconciliationCloseReviewScope:
        """Return the accounting scope shared by bridge and review evidence."""
        return ReconciliationCloseReviewScope(
            tenant_account_reference="tenant-capacity-type",
            legal_entity_reference="entity-capacity-type",
            accounting_book_reference="book-capacity-type",
            bank_account_assignment_reference="bank-capacity-type",
            currency_code="KRW",
        )

    @classmethod
    def _bridge(cls):
        """Return reconciled exact-value bridge evidence for the type-guard test."""
        scope = cls._scope()
        return compute_book_to_bank_bridge(
            BookToBankBridgeInput(
                reconciliation_run_reference="run-capacity-type",
                statement_population_reference="statement-population-capacity-type",
                book_population_reference="book-population-capacity-type",
                currency_code="KRW",
                statement_opening_balance=Decimal("0.00"),
                statement_period_movements=Decimal("10.00"),
                statement_closing_balance=Decimal("10.00"),
                book_opening_balance=Decimal("0.00"),
                posted_cash_book_movements=Decimal("10.00"),
                book_closing_balance=Decimal("10.00"),
                reconciled_book_balance=Decimal("10.00"),
                outstanding_book_items=Decimal("0.00"),
                outstanding_bank_items=Decimal("0.00"),
                tenant_account_reference=scope.tenant_account_reference,
                legal_entity_reference=scope.legal_entity_reference,
                accounting_book_reference=scope.accounting_book_reference,
                bank_account_assignment_reference=scope.bank_account_assignment_reference,
            )
        )

    @staticmethod
    def _decision() -> ReconciliationDecision:
        """Return the durable deterministic decision bound to the malformed match."""
        return ReconciliationDecision(
            statement_entry_reference="statement-anchor",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-anchor",),
            allocated_amount=Decimal("10.00"),
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal.",
            reconciliation_match_reference="match-capacity-type",
        )

    def test_close_review_and_package_reject_text_source_capacity(self) -> None:
        """Both caller-facing validation layers reject a non-Decimal capacity before use."""
        reviewed_match = self._bad_match()
        decision = self._decision()
        projection_input = ReconciliationCloseReviewInput(
            bridge_result=self._bridge(),
            decisions=(decision,),
            expected_statement_entry_references=("statement-anchor",),
            reviewed_matches=(reviewed_match,),
            scope=self._scope(),
        )
        with self.subTest("close review ingestion"):
            with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
                _validate_reviewed_match_population(
                    projection_input,
                    match_decisions=(decision,),
                )

        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-capacity-type",
            legal_entity_reference="entity-capacity-type",
            accounting_book_reference="book-capacity-type",
            bank_account_assignment_reference="bank-capacity-type",
            reconciliation_run_reference="run-capacity-type",
            statement_population_reference="statement-population-capacity-type",
            book_population_reference="book-population-capacity-type",
            currency_code="KRW",
            bank_closing_balance=Decimal("10.00"),
            posted_book_cash_balance=Decimal("10.00"),
            reconciled_balance=Decimal("10.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=1,
            reviewed_match_references=("match-capacity-type",),
            exception_count=0,
            exception_statement_entry_references=(),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=_RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=(reviewed_match,),
        )
        with self.subTest("close package ingestion"):
            with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
                close_package._validate_projection(projection)


if __name__ == "__main__":
    unittest.main()
