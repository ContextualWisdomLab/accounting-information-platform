"""Regression tests for exact reversal command replay and conflict semantics."""

from __future__ import annotations

import unittest
from datetime import date

from accounting_information_platform import (
    AccountingValidationError,
    AccountingPolicy,
    IdempotencyConflictError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class ReversalCommandContractTests(unittest.TestCase):
    """Keep reversal replay bound to immutable command evidence, not only the original journal."""

    def setUp(self) -> None:
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_reversal_contract",
            legal_entity_reference="urn:cwl:legal_entity:reversal_contract",
            accounting_book_reference="urn:cwl:accounting_book:reversal_contract",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping={
                "accounts_receivable": "110100",
                "usage_revenue": "410100",
            },
            accounting_policy_version="ifrs-v1",
            posting_rule_version="reversal-contract-v1",
        )
        self.ledger = PostingLedger()
        self.original = self.ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf611",
                proposal_contract_version=1,
                idempotency_key="reversal-contract-original-v1",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
                source_payload_hash="sha256:" + "a" * 64,
                source_event_references=("urn:cwl:billing:invoice:reversal_contract",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            self.policy,
        )

    def test_exact_reversal_replays_but_changed_reason_conflicts(self) -> None:
        """The implicit reversal command identity cannot replay a changed reason."""
        first = self.ledger.reverse(
            self.original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        replay = self.ledger.reverse(
            self.original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        self.assertEqual(replay, first)

        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                self.original.journal_reference,
                date(2026, 8, 31),
                "duplicate_charge",
                self.policy,
            )

    def test_changed_reversal_date_conflicts_even_after_receipt_cache_loss(self) -> None:
        """The retained reversing journal itself proves the original command hash."""
        self.ledger.reverse(
            self.original.journal_reference,
            date(2026, 8, 30),
            "billing_correction",
            self.policy,
        )
        cache_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference,
            self.original.journal_reference,
        )
        self.ledger._reversal_receipts.pop(cache_key)

        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                self.original.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
            )

    def test_empty_and_backdated_reversal_commands_fail_closed(self) -> None:
        """Reversal identity and temporal order are validated before mutation."""
        with self.assertRaisesRegex(AccountingValidationError, "idempotency key"):
            self.ledger.reverse(
                self.original.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
                reversal_idempotency_key="",
            )
        with self.assertRaisesRegex(AccountingValidationError, "precede"):
            self.ledger.reverse(
                self.original.journal_reference,
                date(2026, 8, 19),
                "billing_correction",
                self.policy,
            )

    def test_missing_reversal_evidence_reconstructs_from_the_posted_journal(self) -> None:
        """A missing cache evidence tuple falls through to the immutable reversing journal."""
        first = self.ledger.reverse(
            self.original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        cache_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference,
            self.original.journal_reference,
        )
        self.ledger._reversal_command_evidence.pop(cache_key)
        replay = self.ledger.reverse(
            self.original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        self.assertEqual(replay, first)


if __name__ == "__main__":
    unittest.main()
