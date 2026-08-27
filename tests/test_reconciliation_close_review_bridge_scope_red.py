"""RED contract for immutable accounting scope bound to book-to-bank bridge evidence.

Close-review evidence must not let a caller relabel a reconciled bridge from one
tenant, legal entity, accounting book, or bank-account assignment as another
same-currency scope.  The bridge itself must bind that accounting/bank scope
before a close review can become suitable evidence.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationCloseReviewInput,
    ReconciliationCloseReviewScope,
    build_reconciliation_close_review,
)


class ReconciliationCloseReviewBridgeScopeTests(unittest.TestCase):
    """Require close-review scope identity to be authoritative bridge evidence."""

    @staticmethod
    def _bridge(
        *,
        run_reference: str = "run-a",
        tenant: str | None = "tenant-a",
        entity: str | None = "entity-a",
        book: str | None = "book-a",
        bank: str | None = "bank-assignment-a",
    ):
        return compute_book_to_bank_bridge(
            BookToBankBridgeInput(
                reconciliation_run_reference=run_reference,
                statement_population_reference=f"statement-{run_reference}",
                book_population_reference=f"book-{run_reference}",
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
    def _scope(*, tenant: str, entity: str, book: str, bank: str):
        return ReconciliationCloseReviewScope(
            tenant_account_reference=tenant,
            legal_entity_reference=entity,
            accounting_book_reference=book,
            bank_account_assignment_reference=bank,
            currency_code="KRW",
        )

    @staticmethod
    def _match() -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="stmt-001",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-001",),
            allocated_amount=Decimal("100.00"),
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal from it.",
        )

    def test_current_bridge_cannot_be_relabelled_to_another_same_currency_scope(self) -> None:
        """A caller-supplied scope cannot authorize a bridge from another accounting scope."""
        foreign_scope = self._scope(
            tenant="tenant-b",
            entity="entity-b",
            book="book-b",
            bank="bank-assignment-b",
        )

        with self.assertRaisesRegex(ValueError, "bridge.*scope|scope.*bridge"):
            build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(),
                    decisions=(self._match(),),
                    expected_statement_entry_references=("stmt-001",),
                    scope=foreign_scope,
                )
            )

    def test_preceding_bridge_cannot_be_relabelled_to_current_scope(self) -> None:
        """Prior-run deltas require prior bridge evidence bound to the identical scope."""
        current_scope = self._scope(
            tenant="tenant-a",
            entity="entity-a",
            book="book-a",
            bank="bank-assignment-a",
        )

        with self.assertRaisesRegex(ValueError, "bridge.*scope|scope.*bridge"):
            build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(run_reference="run-current"),
                    decisions=(self._match(),),
                    expected_statement_entry_references=("stmt-001",),
                    scope=current_scope,
                    preceding_bridge_result=self._bridge(
                        run_reference="run-foreign",
                        tenant="tenant-b",
                        entity="entity-b",
                        book="book-b",
                        bank="bank-assignment-b",
                    ),
                    preceding_scope=current_scope,
                )
            )

    def test_unbound_bridge_cannot_enter_close_review(self) -> None:
        """A legacy or partial bridge without scope identity is not close evidence."""
        current_scope = self._scope(
            tenant="tenant-a",
            entity="entity-a",
            book="book-a",
            bank="bank-assignment-a",
        )
        with self.assertRaisesRegex(ValueError, "bridge scope identity must be bound"):
            build_reconciliation_close_review(
                ReconciliationCloseReviewInput(
                    bridge_result=self._bridge(
                        tenant=None,
                        entity=None,
                        book=None,
                        bank=None,
                    ),
                    decisions=(self._match(),),
                    expected_statement_entry_references=("stmt-001",),
                    scope=current_scope,
                )
            )


if __name__ == "__main__":
    unittest.main()
