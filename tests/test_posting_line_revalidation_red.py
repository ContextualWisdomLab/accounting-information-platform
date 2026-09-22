"""Regression tests for posting-time journal line revalidation."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class PostingLineRevalidationTests(unittest.TestCase):
    """Require current line semantics at the authoritative posting boundary."""

    def setUp(self) -> None:
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            accounting_book_reference="urn:cwl:accounting_book:primary_statutory",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping={
                "cash": "110100",
                "usage_revenue": "410100",
            },
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )

    @staticmethod
    def _proposal() -> JournalProposal:
        return JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf702",
            proposal_contract_version=1,
            idempotency_key="posting-line-revalidation-v1",
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "8" * 64,
            source_event_references=("urn:cwl:billing:invoice:702",),
            lines=(
                JournalLineProposal(
                    line_number=1,
                    account_role_code="cash",
                    debit_amount=Decimal("1000.00"),
                    credit_amount=Decimal("0"),
                ),
                JournalLineProposal(
                    line_number=2,
                    account_role_code="usage_revenue",
                    debit_amount=Decimal("0"),
                    credit_amount=Decimal("1000.00"),
                ),
            ),
        )

    @staticmethod
    def _make_balanced_but_two_sided(proposal: JournalProposal) -> None:
        """Break both line-side invariants while preserving aggregate balance."""
        object.__setattr__(proposal.lines[0], "credit_amount", Decimal("500.00"))
        object.__setattr__(proposal.lines[1], "debit_amount", Decimal("500.00"))

    def test_post_rejects_balanced_lines_changed_to_two_sided_after_validation(self) -> None:
        proposal = self._proposal()
        self._make_balanced_but_two_sided(proposal)
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "exactly one positive"):
            ledger.post(proposal, self.policy)

        self.assertEqual(proposal.debit_total, Decimal("1500.00"))
        self.assertEqual(proposal.credit_total, Decimal("1500.00"))
        self.assertEqual(ledger.journal_count, 0)

    def test_replay_rejects_line_semantics_changed_after_original_post(self) -> None:
        proposal = self._proposal()
        ledger = PostingLedger()
        original_receipt = ledger.post(proposal, self.policy)
        self._make_balanced_but_two_sided(proposal)

        with self.assertRaisesRegex(AccountingValidationError, "exactly one positive"):
            ledger.post(proposal, self.policy)

        self.assertEqual(original_receipt.posting_status_code, "posted")
        self.assertEqual(ledger.journal_count, 1)

    def test_post_rejects_duplicate_line_numbers_changed_after_validation(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal.lines[1], "line_number", 1)
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "line numbers must be unique"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_rejects_line_population_shrunk_after_validation(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal, "lines", (proposal.lines[0],))
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "requires at least two lines"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_accepts_unchanged_valid_line_population(self) -> None:
        ledger = PostingLedger()

        receipt = ledger.post(self._proposal(), self.policy)

        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(receipt.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
