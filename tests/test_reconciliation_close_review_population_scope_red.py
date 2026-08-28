"""RED and coverage contracts for complete close-review populations and scope.

A close-review projection must not become period-close-review eligible from a
caller-selected subset of statement decisions. Preceding-run deltas must also
compare only the same immutable accounting/bank scope; matching currency alone
is insufficient evidence of scope identity. These tests also execute every
fail-closed validation branch that protects that authority boundary.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)
import accounting_information_platform.reconciliation_read_model as read_model


class ReconciliationCloseReviewPopulationScopeTests(unittest.TestCase):
    """Fail closed on incomplete populations and cross-scope prior-run evidence."""

    def _bridge(
        self,
        *,
        run_reference: str,
        statement_reference: str,
        book_reference: str,
        tenant: str = "tenant-a",
        entity: str = "entity-a",
        book: str = "book-a",
        bank: str = "bank-a",
    ):
        return compute_book_to_bank_bridge(
            BookToBankBridgeInput(
                reconciliation_run_reference=run_reference,
                statement_population_reference=statement_reference,
                book_population_reference=book_reference,
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
                tenant_account_reference=tenant,
                legal_entity_reference=entity,
                accounting_book_reference=book,
                bank_account_assignment_reference=bank,
            )
        )

    @staticmethod
    def _match(statement_reference: str) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference=statement_reference,
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-001",),
            allocated_amount=Decimal("100.00"),
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal.",
        )

    @staticmethod
    def _scope(
        *,
        tenant: str = "tenant-a",
        entity: str = "entity-a",
        book: str = "book-a",
        bank: str = "bank-a",
        currency: str = "KRW",
    ):
        Scope = read_model.ReconciliationCloseReviewScope
        return Scope(
            tenant_account_reference=tenant,
            legal_entity_reference=entity,
            accounting_book_reference=book,
            bank_account_assignment_reference=bank,
            currency_code=currency,
        )

    def _input(
        self,
        *,
        decisions: tuple[ReconciliationDecision, ...],
        expected: tuple[str, ...],
        reviewed_match_references=None,
        preceding=None,
        current_scope=None,
        preceding_scope=None,
    ):
        return read_model.ReconciliationCloseReviewInput(
            bridge_result=self._bridge(
                run_reference="run-current",
                statement_reference="statement-current",
                book_reference="book-current",
            ),
            decisions=decisions,
            expected_statement_entry_references=expected,
            reviewed_match_references=(
                tuple(
                    f"reconciliation-match-{index:03d}"
                    for index, decision in enumerate(decisions, start=1)
                    if decision.decision_code == "match"
                )
                if reviewed_match_references is None
                else reviewed_match_references
            ),
            scope=current_scope or self._scope(),
            preceding_bridge_result=preceding,
            preceding_scope=preceding_scope,
        )

    def test_missing_statement_decision_cannot_become_close_review_eligible(self) -> None:
        """A caller-selected subset cannot erase an unresolved statement entry."""
        with self.assertRaisesRegex(ValueError, "statement population"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001", "stmt-002"),
                )
            )

    def test_reviewed_match_identity_container_must_be_canonical(self) -> None:
        with self.assertRaisesRegex(ValueError, "reviewed match identities must be a tuple"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001",),
                    # type: ignore[arg-type]
                    reviewed_match_references=["reconciliation-match-001"],
                )
            )

    def test_reviewed_match_identity_population_must_be_complete_and_unique(self) -> None:
        cases = (
            (("",), "reviewed match identities must be non-empty"),
            (("duplicate", "duplicate"), "reviewed match identities must be unique"),
            ((), "reviewed match identities must exactly cover"),
        )
        for references, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    read_model.build_reconciliation_close_review(
                        self._input(
                            decisions=(self._match("stmt-001"),),
                            expected=("stmt-001",),
                            reviewed_match_references=references,
                        )
                    )

    def test_duplicate_or_extraneous_decision_identity_fails_closed(self) -> None:
        """One immutable statement entry must contribute exactly one decision."""
        with self.assertRaisesRegex(ValueError, "statement population"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"), self._match("stmt-001")),
                    expected=("stmt-001",),
                )
            )

        with self.assertRaisesRegex(ValueError, "statement population"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"), self._match("stmt-extra")),
                    expected=("stmt-001",),
                )
            )

    def test_scope_identity_and_currency_are_validated_before_projection(self) -> None:
        """Incomplete or cross-currency close-review scope is never authoritative."""
        with self.assertRaisesRegex(ValueError, "scope identity"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001",),
                    current_scope=self._scope(tenant="   "),
                )
            )

        with self.assertRaisesRegex(ValueError, "scope currency"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001",),
                    current_scope=self._scope(currency="USD"),
                )
            )

    def test_expected_statement_population_identity_must_be_nonempty_and_unique(self) -> None:
        """The expected immutable population cannot contain blanks or duplicates."""
        with self.assertRaisesRegex(ValueError, "statement population identities"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001", " "),
                )
            )

        with self.assertRaisesRegex(ValueError, "must be unique"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"), self._match("stmt-002")),
                    expected=("stmt-001", "stmt-001"),
                )
            )

    def test_decision_statement_identity_must_be_nonempty(self) -> None:
        """A decision without immutable statement identity cannot enter close evidence."""
        with self.assertRaisesRegex(ValueError, "decision identities"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match(" "),),
                    expected=("stmt-001",),
                )
            )

    def test_preceding_delta_rejects_same_currency_but_different_accounting_scope(self) -> None:
        """A prior run from another book or bank account cannot produce plausible deltas."""
        preceding = self._bridge(
            run_reference="run-previous",
            statement_reference="statement-previous",
            book_reference="book-previous",
            book="book-b",
        )
        with self.assertRaisesRegex(ValueError, "scope"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001",),
                    preceding=preceding,
                    current_scope=self._scope(book="book-a", bank="bank-a"),
                    preceding_scope=self._scope(book="book-b", bank="bank-a"),
                )
            )

    def test_preceding_bridge_and_scope_must_be_supplied_together(self) -> None:
        """Historical deltas cannot be computed from an unscoped or scope-only prior run."""
        preceding = self._bridge(
            run_reference="run-previous",
            statement_reference="statement-previous",
            book_reference="book-previous",
        )
        with self.assertRaisesRegex(ValueError, "preceding reconciliation scope"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001",),
                    preceding_scope=self._scope(),
                )
            )

        with self.assertRaisesRegex(ValueError, "preceding reconciliation scope"):
            read_model.build_reconciliation_close_review(
                self._input(
                    decisions=(self._match("stmt-001"),),
                    expected=("stmt-001",),
                    preceding=preceding,
                )
            )

    def test_same_scope_preceding_run_remains_eligible_for_exact_deltas(self) -> None:
        """Legitimate same-scope historical comparison remains available."""
        preceding = self._bridge(
            run_reference="run-previous",
            statement_reference="statement-previous",
            book_reference="book-previous",
        )
        projection = read_model.build_reconciliation_close_review(
            self._input(
                decisions=(self._match("stmt-001"),),
                expected=("stmt-001",),
                preceding=preceding,
                current_scope=self._scope(),
                preceding_scope=self._scope(),
            )
        )
        self.assertTrue(projection.suitable_for_period_close_review)
        self.assertEqual(projection.unexplained_difference_change, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
