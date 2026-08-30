"""RED contract for the first buyer-visible reconciliation close-review projection.

The projection is read-only. It must expose exact bridge values, deterministic
match/exception summaries, changes from a preceding run, and an explicit next
action without posting journals, approving reconciliation, or mutating statement
evidence. JSON and CSV exports must preserve monetary values as exact decimal
strings rather than floating-point numbers.
"""

from __future__ import annotations

import csv
import importlib
import io
import json
import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)


class ReconciliationCloseReviewProjectionTests(unittest.TestCase):
    """Require exact values, provenance, operator action, and export parity."""

    def _api(self):
        module = importlib.import_module(
            "accounting_information_platform.reconciliation_read_model"
        )
        return (
            module.ReconciliationCloseReviewInput,
            module.ReconciliationCloseReviewScope,
            module.ReconciliationReviewedMatch,
            module.build_reconciliation_close_review,
            module.render_reconciliation_close_review_json,
            module.render_reconciliation_close_review_csv,
        )

    @staticmethod
    def _reviewed_match(ReviewedMatch, statement_reference: str = "stmt-001"):
        return ReviewedMatch(
            reconciliation_match_reference="reconciliation-match-001",
            statement_entry_reference=statement_reference,
            journal_reference="journal-001",
            allocated_amount=Decimal("100.00"),
        )

    def _bridge(self, **overrides):
        values = {
            "reconciliation_run_reference": "run-current",
            "statement_population_reference": "statement-current",
            "book_population_reference": "book-current",
            "currency_code": "KRW",
            "statement_opening_balance": Decimal("1000.00"),
            "statement_period_movements": Decimal("250.00"),
            "statement_closing_balance": Decimal("1250.00"),
            "book_opening_balance": Decimal("900.00"),
            "posted_cash_book_movements": Decimal("300.00"),
            "book_closing_balance": Decimal("1200.00"),
            "reconciled_book_balance": Decimal("1200.00"),
            "outstanding_book_items": Decimal("100.00"),
            "outstanding_bank_items": Decimal("50.00"),
            "tenant_account_reference": "tenant-a",
            "legal_entity_reference": "entity-a",
            "accounting_book_reference": "book-a",
            "bank_account_assignment_reference": "bank-assignment-a",
        }
        values.update(overrides)
        return compute_book_to_bank_bridge(BookToBankBridgeInput(**values))

    @staticmethod
    def _scope(Scope):
        return Scope(
            tenant_account_reference="tenant-a",
            legal_entity_reference="entity-a",
            accounting_book_reference="book-a",
            bank_account_assignment_reference="bank-assignment-a",
            currency_code="KRW",
        )

    @staticmethod
    def _match(statement_reference: str = "stmt-001") -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference=statement_reference,
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-001",),
            allocated_amount=Decimal("100.00"),
            exception_code=None,
            next_action=(
                "Review and record this deterministic reconciliation proposal; "
                "do not post a journal from it."
            ),
            reconciliation_match_reference="reconciliation-match-001",
        )

    @staticmethod
    def _exception(statement_reference: str = "stmt-002") -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference=statement_reference,
            decision_code="abstain",
            rule_code=None,
            matched_journal_references=(),
            allocated_amount=Decimal("0"),
            exception_code="no_candidate",
            next_action=(
                "Review unmatched statement evidence and create an authorized "
                "exception or adjusting-journal proposal if required."
            ),
        )

    def test_projection_exposes_exact_values_provenance_and_unresolved_next_action(self) -> None:
        """A controller can see exact bridge facts and the work still blocking close review."""
        ProjectionInput, Scope, ReviewedMatch, build_projection, _, _ = self._api()
        preceding = self._bridge(
            reconciliation_run_reference="run-previous",
            statement_population_reference="statement-previous",
            book_population_reference="book-previous",
            outstanding_book_items=Decimal("120.00"),
            outstanding_bank_items=Decimal("70.00"),
        )
        scope = self._scope(Scope)

        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(),
                decisions=(self._match(), self._exception()),
                expected_statement_entry_references=("stmt-001", "stmt-002"),
                reviewed_matches=(self._reviewed_match(ReviewedMatch),),
                scope=scope,
                preceding_bridge_result=preceding,
                preceding_scope=scope,
            )
        )

        self.assertEqual(projection.tenant_account_reference, "tenant-a")
        self.assertEqual(projection.legal_entity_reference, "entity-a")
        self.assertEqual(projection.accounting_book_reference, "book-a")
        self.assertEqual(
            projection.bank_account_assignment_reference, "bank-assignment-a"
        )
        self.assertEqual(projection.reconciliation_run_reference, "run-current")
        self.assertEqual(projection.statement_population_reference, "statement-current")
        self.assertEqual(projection.book_population_reference, "book-current")
        self.assertEqual(projection.currency_code, "KRW")
        self.assertEqual(projection.bank_closing_balance, Decimal("1250.00"))
        self.assertEqual(projection.posted_book_cash_balance, Decimal("1200.00"))
        self.assertEqual(projection.reconciled_balance, Decimal("1200.00"))
        self.assertEqual(projection.outstanding_bank_items, Decimal("50.00"))
        self.assertEqual(projection.outstanding_book_items, Decimal("100.00"))
        self.assertEqual(projection.unexplained_difference, Decimal("0"))
        self.assertEqual(projection.safely_matchable_candidate_count, 1)
        self.assertEqual(projection.exception_count, 1)
        self.assertEqual(projection.unexplained_difference_change, Decimal("0"))
        self.assertEqual(projection.outstanding_bank_items_change, Decimal("-20.00"))
        self.assertEqual(projection.outstanding_book_items_change, Decimal("-20.00"))
        self.assertFalse(projection.suitable_for_period_close_review)
        self.assertIn("resolve", projection.next_action.lower())
        self.assertIn("stmt-002", projection.exception_statement_entry_references)

    def test_fully_reconciled_projection_is_close_review_candidate_not_an_approval(self) -> None:
        """A clean projection may be suitable evidence but must not claim approval or posting."""
        ProjectionInput, Scope, ReviewedMatch, build_projection, _, _ = self._api()
        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(),
                decisions=(self._match(),),
                expected_statement_entry_references=("stmt-001",),
                reviewed_matches=(self._reviewed_match(ReviewedMatch),),
                scope=self._scope(Scope),
            )
        )

        self.assertTrue(projection.suitable_for_period_close_review)
        self.assertEqual(projection.exception_count, 0)
        self.assertIn("period-close review", projection.next_action.lower())
        self.assertNotIn("approved", projection.next_action.lower())
        self.assertNotIn("post journal", projection.next_action.lower())

    def test_non_tying_bridge_never_emits_success_shaped_close_review_evidence(self) -> None:
        """An exact bridge difference keeps the public projection fail-closed."""
        ProjectionInput, Scope, ReviewedMatch, build_projection, _, _ = self._api()
        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(outstanding_bank_items=Decimal("50.01")),
                decisions=(self._match(),),
                expected_statement_entry_references=("stmt-001",),
                reviewed_matches=(self._reviewed_match(ReviewedMatch),),
                scope=self._scope(Scope),
            )
        )

        self.assertFalse(projection.suitable_for_period_close_review)
        self.assertEqual(projection.unexplained_difference, Decimal("-0.01"))
        self.assertIn("bridge", projection.next_action.lower())
        self.assertIn("0.01", projection.next_action)

    def test_json_and_csv_exports_preserve_exact_decimal_strings_and_next_action(self) -> None:
        """Exports keep monetary evidence exact and visible without hover-only formatting."""
        ProjectionInput, Scope, ReviewedMatch, build_projection, render_json, render_csv = self._api()
        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(),
                decisions=(self._match(),),
                expected_statement_entry_references=("stmt-001",),
                reviewed_matches=(self._reviewed_match(ReviewedMatch),),
                scope=self._scope(Scope),
            )
        )

        json_payload = json.loads(render_json(projection))
        self.assertEqual(json_payload["tenant_account_reference"], "tenant-a")
        self.assertEqual(json_payload["accounting_book_reference"], "book-a")
        self.assertEqual(json_payload["bank_closing_balance"], "1250.00")
        self.assertEqual(json_payload["posted_book_cash_balance"], "1200.00")
        self.assertEqual(json_payload["unexplained_difference"], "0")
        self.assertEqual(
            json_payload["reviewed_match_evidence"][0]["allocated_amount"],
            "100.00",
        )
        self.assertEqual(
            json_payload["reviewed_match_evidence"][0]["journal_reference"],
            "journal-001",
        )
        self.assertIn("next_action", json_payload)

        rows = list(csv.DictReader(io.StringIO(render_csv(projection))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tenant_account_reference"], "tenant-a")
        self.assertEqual(rows[0]["bank_account_assignment_reference"], "bank-assignment-a")
        self.assertEqual(rows[0]["bank_closing_balance"], "1250.00")
        self.assertEqual(rows[0]["posted_book_cash_balance"], "1200.00")
        self.assertEqual(rows[0]["unexplained_difference"], "0")
        self.assertEqual(rows[0]["suitable_for_period_close_review"], "true")
        self.assertIn('"allocated_amount":"100.00"', rows[0]["reviewed_match_evidence"])
        self.assertTrue(rows[0]["next_action"])


if __name__ == "__main__":
    unittest.main()
