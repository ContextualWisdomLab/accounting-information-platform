"""RED contracts for current-head reconciliation review findings.

These regressions keep HTTP validation semantics and reviewed allocation provenance
fail-closed without broadening posting, approval, close, or policy authority.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.http_api import _reconciliation_run_status
from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)
from accounting_information_platform.reconciliation_read_model import (
    build_reconciliation_close_review,
    ReconciliationAllocationEvidence,
    ReconciliationCloseReviewInput,
    ReconciliationCloseReviewScope,
    ReconciliationReviewedMatch,
)


class CurrentHeadReviewRegressionTests(unittest.TestCase):
    """Prove the exact current review defects before their causal repairs."""

    def test_missing_post_body_field_remains_semantic_422(self) -> None:
        """POST-body validation must not be classified as a malformed query request."""
        error = AccountingValidationError(
            "matching_policy_version is required and must be a canonical string."
        )
        self.assertEqual(_reconciliation_run_status(error), 422)
        self.assertEqual(
            _reconciliation_run_status(
                AccountingValidationError("reconciliation_run_id must be a UUID.")
            ),
            400,
        )

    def test_reviewed_match_rejects_uncovered_statement_allocation_source(self) -> None:
        """A close review cannot carry a statement source absent from its decisions."""
        decision = ReconciliationDecision(
            statement_entry_reference="stmt-001",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-001",),
            allocated_amount=Decimal("100.00"),
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal.",
            reconciliation_match_reference="match-001",
        )
        reviewed_match = ReconciliationReviewedMatch(
            reconciliation_match_reference="match-001",
            candidate_reference="candidate-001",
            candidate_statement_reference="stmt-001",
            candidate_journal_reference="journal-001",
            statement_amount=Decimal("100.00"),
            journal_amount=Decimal("150.00"),
            rule_code="provider_reference",
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="statement-allocation-001",
                    source_reference="stmt-001",
                    allocated_amount=Decimal("100.00"),
                    source_capacity=Decimal("100.00"),
                ),
                ReconciliationAllocationEvidence(
                    allocation_reference="statement-allocation-phantom",
                    source_reference="stmt-phantom",
                    allocated_amount=Decimal("50.00"),
                    source_capacity=Decimal("50.00"),
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="journal-allocation-001",
                    source_reference="journal-001",
                    allocated_amount=Decimal("150.00"),
                    source_capacity=Decimal("150.00"),
                ),
            ),
        )
        bridge = compute_book_to_bank_bridge(
            BookToBankBridgeInput(
                reconciliation_run_reference="run-001",
                statement_population_reference="statement-population-001",
                book_population_reference="book-population-001",
                currency_code="KRW",
                statement_opening_balance=Decimal("1000.00"),
                statement_period_movements=Decimal("250.00"),
                statement_closing_balance=Decimal("1250.00"),
                book_opening_balance=Decimal("900.00"),
                posted_cash_book_movements=Decimal("300.00"),
                book_closing_balance=Decimal("1200.00"),
                reconciled_book_balance=Decimal("1200.00"),
                outstanding_book_items=Decimal("100.00"),
                outstanding_bank_items=Decimal("50.00"),
                tenant_account_reference="tenant-a",
                legal_entity_reference="entity-a",
                accounting_book_reference="book-a",
                bank_account_assignment_reference="bank-a",
            )
        )
        projection_input = ReconciliationCloseReviewInput(
            bridge_result=bridge,
            decisions=(decision,),
            expected_statement_entry_references=("stmt-001",),
            reviewed_matches=(reviewed_match,),
            scope=ReconciliationCloseReviewScope(
                tenant_account_reference="tenant-a",
                legal_entity_reference="entity-a",
                accounting_book_reference="book-a",
                bank_account_assignment_reference="bank-a",
                currency_code="KRW",
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "cover every normalized statement allocation",
        ):
            build_reconciliation_close_review(projection_input)


if __name__ == "__main__":
    unittest.main()
