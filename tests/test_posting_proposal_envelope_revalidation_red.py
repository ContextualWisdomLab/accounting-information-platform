"""Regression tests for posting-time proposal-envelope revalidation."""

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


class PostingProposalEnvelopeRevalidationTests(unittest.TestCase):
    """Require current proposal identity and provenance before posting or replay."""

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
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf703",
            proposal_contract_version=1,
            idempotency_key="posting-envelope-revalidation-v1",
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "9" * 64,
            source_event_references=("urn:cwl:billing:invoice:703",),
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

    def test_post_rejects_proposal_id_changed_after_validation(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal, "proposal_id", "not-a-uuid")
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "proposal_id must be a UUID"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_rejects_idempotency_key_removed_after_validation(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal, "idempotency_key", "")
        ledger = PostingLedger()

        with self.assertRaisesRegex(
            AccountingValidationError, "idempotency_key must be a non-empty string"
        ):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_rejects_payload_hash_changed_after_validation(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal, "source_payload_hash", "sha256:not-canonical")
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "source_payload_hash must be canonical sha256"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_rejects_source_population_removed_after_validation(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal, "source_event_references", ())
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "at least one source event reference"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_replay_rejects_invalid_envelope_before_cached_receipt(self) -> None:
        proposal = self._proposal()
        ledger = PostingLedger()
        original_receipt = ledger.post(proposal, self.policy)
        object.__setattr__(proposal, "proposal_id", "not-a-uuid")

        with self.assertRaisesRegex(AccountingValidationError, "proposal_id must be a UUID"):
            ledger.post(proposal, self.policy)

        self.assertEqual(original_receipt.posting_status_code, "posted")
        self.assertEqual(ledger.journal_count, 1)

    def test_post_accepts_unchanged_valid_proposal_envelope(self) -> None:
        ledger = PostingLedger()

        receipt = ledger.post(self._proposal(), self.policy)

        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(receipt.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
