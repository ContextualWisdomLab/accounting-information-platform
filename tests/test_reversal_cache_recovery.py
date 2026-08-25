"""Regression for deterministic reversal replay when derived receipt caches are absent."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date

from accounting_information_platform import (
    AccountingPolicy,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class ReversalCacheRecoveryTests(unittest.TestCase):
    """Treat the retained reversal journal as authoritative over derived in-memory caches."""

    def test_existing_reversal_replays_after_cache_loss_even_if_period_is_now_closed(self) -> None:
        """A cache miss must not re-run current-period admission for an already-posted reversal."""
        policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_reversal_cache",
            legal_entity_reference="urn:cwl:legal_entity:reversal_cache",
            accounting_book_reference="urn:cwl:accounting_book:reversal_cache",
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
            posting_rule_version="reversal-cache-v1",
        )
        ledger = PostingLedger()
        original = ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf760",
                proposal_contract_version=1,
                idempotency_key="reversal-cache-post-v1",
                tenant_reference=policy.tenant_reference,
                legal_entity_reference=policy.legal_entity_reference,
                intended_book_role_code=policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 31),
                accounting_date=date(2026, 8, 31),
                source_payload_hash="sha256:" + "d" * 64,
                source_event_references=("urn:cwl:event:reversal_cache",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            policy,
        )
        reversal_key = "reversal-cache-command-v1"
        posted_reversal = ledger.reverse(
            original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            policy,
            reversal_idempotency_key=reversal_key,
        )

        cache_key = ledger._tenant_cache_key(policy.tenant_reference, original.journal_reference)
        ledger._reversal_receipts.pop(cache_key)
        ledger._reversal_command_evidence.pop(cache_key)
        closed_policy = replace(
            policy,
            open_period_start=date(2026, 9, 1),
            open_period_end=date(2026, 9, 30),
        )

        replay = ledger.reverse(
            original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            closed_policy,
            reversal_idempotency_key=reversal_key,
        )

        self.assertEqual(replay, posted_reversal)
        self.assertEqual(ledger.journal_count, 2)


if __name__ == "__main__":
    unittest.main()
