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
from dataclasses import replace
from decimal import Decimal, localcontext

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
            module.ReconciliationAllocationEvidence,
            module.ReconciliationReviewedMatch,
            module.build_reconciliation_close_review,
            module.render_reconciliation_close_review_json,
            module.render_reconciliation_close_review_csv,
        )

    @staticmethod
    def _reviewed_match(
        Allocation,
        ReviewedMatch,
        statement_reference: str = "stmt-001",
    ):
        return ReviewedMatch(
            reconciliation_match_reference="reconciliation-match-001",
            candidate_reference="candidate-001",
            candidate_statement_reference=statement_reference,
            candidate_journal_reference="journal-001",
            statement_amount=Decimal("100.00"),
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                Allocation(
                    allocation_reference="statement-allocation-001",
                    source_reference=statement_reference,
                    allocated_amount=Decimal("100.00"),
                ),
            ),
            journal_allocations=(
                Allocation(
                    allocation_reference="journal-allocation-001",
                    source_reference="journal-001",
                    allocated_amount=Decimal("100.00"),
                ),
            ),
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
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, _, _ = self._api()
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
                reviewed_matches=(self._reviewed_match(Allocation, ReviewedMatch),),
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
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, _, _ = self._api()
        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(),
                decisions=(self._match(),),
                expected_statement_entry_references=("stmt-001",),
                reviewed_matches=(self._reviewed_match(Allocation, ReviewedMatch),),
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
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, _, _ = self._api()
        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(outstanding_bank_items=Decimal("50.01")),
                decisions=(self._match(),),
                expected_statement_entry_references=("stmt-001",),
                reviewed_matches=(self._reviewed_match(Allocation, ReviewedMatch),),
                scope=self._scope(Scope),
            )
        )

        self.assertFalse(projection.suitable_for_period_close_review)
        self.assertEqual(projection.unexplained_difference, Decimal("-0.01"))
        self.assertIn("bridge", projection.next_action.lower())
        self.assertIn("0.01", projection.next_action)

    def test_json_and_csv_exports_preserve_exact_decimal_strings_and_next_action(self) -> None:
        """Exports keep monetary evidence exact and visible without hover-only formatting."""
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, render_json, render_csv = self._api()
        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(),
                decisions=(self._match(),),
                expected_statement_entry_references=("stmt-001",),
                reviewed_matches=(self._reviewed_match(Allocation, ReviewedMatch),),
                scope=self._scope(Scope),
            )
        )

        json_payload = json.loads(render_json(projection))
        self.assertEqual(json_payload["schema_version"], 2)
        self.assertEqual(json_payload["tenant_account_reference"], "tenant-a")
        self.assertEqual(json_payload["accounting_book_reference"], "book-a")
        self.assertEqual(json_payload["bank_closing_balance"], "1250.00")
        self.assertEqual(json_payload["posted_book_cash_balance"], "1200.00")
        self.assertEqual(json_payload["unexplained_difference"], "0")
        self.assertEqual(
            json_payload["reviewed_match_evidence"][0]["statement_allocations"][0][
                "allocated_amount"
            ],
            "100.00",
        )
        self.assertEqual(
            json_payload["reviewed_match_evidence"][0]["journal_allocations"][0][
                "source_reference"
            ],
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
        self.assertIn('\"allocated_amount\":\"100.00\"', rows[0]["reviewed_match_evidence"])
        self.assertTrue(rows[0]["next_action"])

    def test_close_review_preserves_one_statement_to_many_journal_allocations(self) -> None:
        """A split match keeps every normalized journal allocation in close evidence."""
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, _, _ = self._api()
        decision = ReconciliationDecision(
            statement_entry_reference="stmt-001",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-001", "journal-002"),
            allocated_amount=Decimal("150.00"),
            exception_code=None,
            next_action="Review and record this deterministic reconciliation proposal; do not post a journal from it.",
            reconciliation_match_reference="reconciliation-match-split",
            contract_version="reconciliation-decision/v2",
        )
        reviewed_match = ReviewedMatch(
            reconciliation_match_reference="reconciliation-match-split",
            candidate_reference="candidate-split",
            candidate_statement_reference="stmt-001",
            candidate_journal_reference="journal-001",
            statement_amount=Decimal("150.00"),
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                Allocation(
                    "statement-allocation-split",
                    "stmt-001",
                    Decimal("150.00"),
                    Decimal("150.00"),
                ),
            ),
            journal_allocations=(
                Allocation(
                    "journal-allocation-001",
                    "journal-001",
                    Decimal("100.00"),
                    Decimal("100.00"),
                ),
                Allocation(
                    "journal-allocation-002",
                    "journal-002",
                    Decimal("50.00"),
                    Decimal("50.00"),
                ),
            ),
        )

        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(),
                decisions=(decision,),
                expected_statement_entry_references=("stmt-001",),
                reviewed_matches=(reviewed_match,),
                scope=self._scope(Scope),
            )
        )

        self.assertEqual(len(projection.reviewed_match_evidence), 1)
        self.assertEqual(
            tuple(
                allocation.source_reference
                for allocation in projection.reviewed_match_evidence[0].journal_allocations
            ),
            ("journal-001", "journal-002"),
        )

    def test_close_review_preserves_many_statement_to_one_journal_allocations(self) -> None:
        """An aggregate match binds each statement decision to one complete match record."""
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, _, _ = self._api()
        decisions = tuple(
            ReconciliationDecision(
                statement_entry_reference=statement_reference,
                decision_code="match",
                rule_code="provider_reference",
                matched_journal_references=("journal-aggregate",),
                allocated_amount=amount,
                exception_code=None,
                next_action="Review and record this deterministic reconciliation proposal; do not post a journal from it.",
                reconciliation_match_reference="reconciliation-match-aggregate",
                contract_version="reconciliation-decision/v2",
            )
            for statement_reference, amount in (
                ("stmt-001", Decimal("60.00")),
                ("stmt-002", Decimal("40.00")),
            )
        )
        reviewed_match = ReviewedMatch(
            reconciliation_match_reference="reconciliation-match-aggregate",
            candidate_reference="candidate-aggregate",
            candidate_statement_reference="stmt-001",
            candidate_journal_reference="journal-aggregate",
            statement_amount=Decimal("60.00"),
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                Allocation(
                    "statement-allocation-001",
                    "stmt-001",
                    Decimal("60.00"),
                    Decimal("60.00"),
                ),
                Allocation(
                    "statement-allocation-002",
                    "stmt-002",
                    Decimal("40.00"),
                    Decimal("40.00"),
                ),
            ),
            journal_allocations=(
                Allocation(
                    "journal-allocation-aggregate",
                    "journal-aggregate",
                    Decimal("100.00"),
                    Decimal("100.00"),
                ),
            ),
        )

        projection = build_projection(
            ProjectionInput(
                bridge_result=self._bridge(),
                decisions=decisions,
                expected_statement_entry_references=("stmt-001", "stmt-002"),
                reviewed_matches=(reviewed_match,),
                scope=self._scope(Scope),
            )
        )

        self.assertEqual(projection.safely_matchable_candidate_count, 1)
        self.assertEqual(
            tuple(
                allocation.source_reference
                for allocation in projection.reviewed_match_evidence[0].statement_allocations
            ),
            ("stmt-001", "stmt-002"),
        )

    def test_close_review_rejects_malformed_reviewed_match_evidence(self) -> None:
        """Malformed reviewed evidence cannot enter the buyer-facing projection."""
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, _, _ = self._api()
        valid_match = self._reviewed_match(Allocation, ReviewedMatch)

        extra_journal_allocation = Allocation(
            allocation_reference="journal-allocation-002",
            source_reference="journal-002",
            allocated_amount=Decimal("1.00"),
            source_capacity=Decimal("1.00"),
        )
        cases = (
            (
                replace(valid_match, candidate_reference=" candidate-001"),
                self._match(),
                "candidate facts must be canonical",
            ),
            (
                replace(valid_match, statement_allocations=()),
                self._match(),
                "non-empty tuples",
            ),
            (
                replace(valid_match, statement_allocations=("not-structured",)),
                self._match(),
                "structured evidence objects",
            ),
            (
                replace(
                    valid_match,
                    statement_allocations=(
                        replace(
                            valid_match.statement_allocations[0],
                            source_reference=" stmt-001",
                        ),
                    ),
                ),
                self._match(),
                "identities must be canonical",
            ),
            (
                replace(
                    valid_match,
                    statement_allocations=(
                        replace(
                            valid_match.statement_allocations[0],
                            allocated_amount=Decimal("0"),
                        ),
                    ),
                ),
                self._match(),
                "positive exact Decimal",
            ),
            (
                replace(
                    valid_match,
                    statement_allocations=(
                        valid_match.statement_allocations[0],
                        replace(
                            valid_match.statement_allocations[0],
                            allocation_reference="statement-allocation-001",
                            source_reference="stmt-002",
                        ),
                    ),
                ),
                self._match(),
                "identities must be unique",
            ),
            (
                replace(valid_match, candidate_statement_reference="stmt-missing"),
                self._match(),
                "represented in their allocations",
            ),
            (
                replace(
                    valid_match,
                    statement_allocations=(
                        replace(
                            valid_match.statement_allocations[0],
                            allocated_amount=Decimal("99.00"),
                        ),
                    ),
                    journal_allocations=(
                        replace(
                            valid_match.journal_allocations[0],
                            allocated_amount=Decimal("99.00"),
                        ),
                    ),
                ),
                self._match(),
                "bind every decision to statement allocations",
            ),
            (
                valid_match,
                replace(self._match(), matched_journal_references=("journal-missing",)),
                "bind every decision to journal allocations",
            ),
            (
                replace(
                    valid_match,
                    statement_allocations=(
                        replace(
                            valid_match.statement_allocations[0],
                            source_capacity=Decimal("100.00"),
                        ),
                    ),
                    journal_allocations=(
                        replace(
                            valid_match.journal_allocations[0],
                            allocated_amount=Decimal("99.00"),
                            source_capacity=Decimal("100.00"),
                        ),
                        extra_journal_allocation,
                    ),
                ),
                self._match(),
                "cover every normalized journal allocation",
            ),
        )
        for reviewed_match, decision, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_projection(
                        ProjectionInput(
                            bridge_result=self._bridge(),
                            decisions=(decision,),
                            expected_statement_entry_references=("stmt-001",),
                            reviewed_matches=(reviewed_match,),
                            scope=self._scope(Scope),
                        )
                    )

    def test_close_review_deltas_preserve_large_decimal_differences_at_low_precision(self) -> None:
        """Preceding-run deltas must retain minor-unit differences in large balances."""
        ProjectionInput, Scope, Allocation, ReviewedMatch, build_projection, _, _ = self._api()
        huge_reconciled = "10000000000000000000000000.000000"
        current_outstanding_book = "12345678901234567890123456.000000"
        preceding_outstanding_book = "12345678901234567890123455.997000"

        def high_bridge(run: str, outstanding_book: str):
            statement_closing = (
                "22345678901234567890123456.000000"
                if outstanding_book == current_outstanding_book
                else "22345678901234567890123455.997000"
            )
            return self._bridge(
                reconciliation_run_reference=run,
                statement_population_reference=f"statement-{run}",
                book_population_reference=f"book-{run}",
                statement_opening_balance=Decimal(statement_closing),
                statement_period_movements=Decimal("0.000000"),
                statement_closing_balance=Decimal(statement_closing),
                book_opening_balance=Decimal(huge_reconciled),
                posted_cash_book_movements=Decimal("0.000000"),
                book_closing_balance=Decimal(huge_reconciled),
                reconciled_book_balance=Decimal(huge_reconciled),
                outstanding_book_items=Decimal(outstanding_book),
                outstanding_bank_items=Decimal("0.000000"),
            )

        scope = self._scope(Scope)
        with localcontext() as context:
            context.prec = 6
            projection = build_projection(
                ProjectionInput(
                    bridge_result=high_bridge("run-current", current_outstanding_book),
                    decisions=(self._match(),),
                    expected_statement_entry_references=("stmt-001",),
                    reviewed_matches=(self._reviewed_match(Allocation, ReviewedMatch),),
                    scope=scope,
                    preceding_bridge_result=high_bridge(
                        "run-previous", preceding_outstanding_book
                    ),
                    preceding_scope=scope,
                )
            )

        self.assertEqual(projection.outstanding_book_items_change, Decimal("0.003000"))
        self.assertEqual(projection.unexplained_difference_change, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
