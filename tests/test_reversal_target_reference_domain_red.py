"""Regression tests for reversal target-reference admission."""

from __future__ import annotations

import unittest
from datetime import date

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class ReversalTargetReferenceDomainRedTests(unittest.TestCase):
    """Require a canonical journal reference before reversal command hashing or lookup."""

    def setUp(self) -> None:
        """Post one canonical journal eligible for a same-period reversal."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_reversal_reference",
            legal_entity_reference="urn:cwl:legal_entity:reversal_reference",
            accounting_book_reference="urn:cwl:accounting_book:reversal_reference",
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
            posting_rule_version="reversal-reference-v1",
        )
        self.ledger = PostingLedger()
        receipt = self.ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf815",
                proposal_contract_version=1,
                idempotency_key="reversal-reference-original-v1",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
                source_payload_hash="sha256:" + "e" * 64,
                source_event_references=("urn:cwl:billing:invoice:reversal_reference",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            self.policy,
        )
        self.journal_reference = receipt.journal_reference

    def test_reversal_rejects_malformed_target_reference(self) -> None:
        """Malformed target identity must fail as identity admission, not missing journal."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "journal reference must be a CWL URN",
        ):
            self.ledger.reverse(
                journal_reference="accounting:general_journal:malformed",
                reversal_date=date(2026, 8, 21),
                reversal_reason_code="correction",
                policy=self.policy,
                reversal_idempotency_key="reversal-reference-command-v1",
            )

        self.assertEqual(self.ledger.journal_count, 1)

    def test_canonical_target_reference_preserves_reversal(self) -> None:
        """A canonical retained journal reference still produces one append-only reversal."""
        receipt = self.ledger.reverse(
            journal_reference=self.journal_reference,
            reversal_date=date(2026, 8, 21),
            reversal_reason_code="correction",
            policy=self.policy,
            reversal_idempotency_key="reversal-reference-command-v1",
        )

        self.assertEqual(receipt.reversal_of_journal_reference, self.journal_reference)
        self.assertEqual(self.ledger.journal_count, 2)


if __name__ == "__main__":
    unittest.main()
