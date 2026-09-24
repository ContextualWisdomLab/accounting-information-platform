"""Regression tests for posting idempotency-key runtime admission."""

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


class _HostileIdempotencyKey(str):
    """Fail if caller-defined hashing reaches the posting cache boundary."""

    def __hash__(self) -> int:
        """Expose missing repository admission before cache lookup."""
        raise AssertionError("caller-defined idempotency hashing executed")


class PostingIdempotencyKeyRuntimeDomainRedTests(unittest.TestCase):
    """Require repository-owned string identity before posting or replay."""

    def setUp(self) -> None:
        """Create one canonical policy for deterministic posting controls."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_posting_idempotency",
            legal_entity_reference="urn:cwl:legal_entity:posting_idempotency",
            accounting_book_reference="urn:cwl:accounting_book:posting_idempotency",
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
            posting_rule_version="posting-idempotency-v1",
        )

    def _proposal(self, *, idempotency_key: object = "posting-idempotency-v1") -> JournalProposal:
        """Build one balanced proposal while varying only command identity type."""
        return JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf816",
            proposal_contract_version=1,
            idempotency_key=idempotency_key,  # type: ignore[arg-type]
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "f" * 64,
            source_event_references=("urn:cwl:billing:invoice:posting_idempotency",),
            lines=(
                JournalLineProposal(1, "accounts_receivable", "100", "0"),
                JournalLineProposal(2, "usage_revenue", "0", "100"),
            ),
        )

    def test_constructor_rejects_non_string_idempotency_key(self) -> None:
        """A truthy non-string must not become durable posting identity."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "idempotency_key must be a non-empty string",
        ):
            self._proposal(idempotency_key=42)

    def test_post_revalidates_hostile_string_subclass_before_cache_lookup(self) -> None:
        """Posting-time envelope admission must reject caller-defined key behavior."""
        proposal = self._proposal()
        object.__setattr__(
            proposal,
            "idempotency_key",
            _HostileIdempotencyKey("posting-idempotency-v1"),
        )
        ledger = PostingLedger()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "idempotency_key must be a non-empty string",
        ):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_mutated_replay_key_fails_before_cached_or_immutable_journal_paths(self) -> None:
        """A cached receipt must not authorize a runtime-invalid current key."""
        proposal = self._proposal()
        ledger = PostingLedger()
        receipt = ledger.post(proposal, self.policy)
        object.__setattr__(proposal, "idempotency_key", 42)

        with self.assertRaisesRegex(
            AccountingValidationError,
            "idempotency_key must be a non-empty string",
        ):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 1)
        self.assertEqual(receipt.posting_status_code, "posted")

    def test_exact_built_in_string_preserves_idempotent_replay(self) -> None:
        """Canonical string identity still returns the exact prior receipt."""
        proposal = self._proposal()
        ledger = PostingLedger()

        first = ledger.post(proposal, self.policy)
        second = ledger.post(proposal, self.policy)

        self.assertEqual(first, second)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
