"""Regression tests for source-payload-hash runtime admission."""

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


class _HostilePayloadHash(str):
    """Expose caller-defined comparison if a hash subclass reaches replay control."""

    def __eq__(self, other: object) -> bool:
        """Fail if replay compares caller-defined hash behavior."""
        raise AssertionError("caller-defined source-payload equality executed")

    def __ne__(self, other: object) -> bool:
        """Fail if replay compares caller-defined hash behavior."""
        raise AssertionError("caller-defined source-payload inequality executed")


class SourcePayloadHashRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in canonical SHA-256 strings as posting provenance."""

    def setUp(self) -> None:
        """Prepare one canonical proposal and policy for posting and replay."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_payload_hash",
            legal_entity_reference="urn:cwl:legal_entity:payload_hash",
            accounting_book_reference="urn:cwl:accounting_book:payload_hash",
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
            posting_rule_version="payload-hash-v1",
        )
        self.canonical_hash = "sha256:" + "d" * 64
        self.proposal = JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf820",
            proposal_contract_version=1,
            idempotency_key="payload-hash-command-v1",
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash=self.canonical_hash,
            source_event_references=("urn:cwl:billing:invoice:payload_hash",),
            lines=(
                JournalLineProposal(1, "accounts_receivable", "100", "0"),
                JournalLineProposal(2, "usage_revenue", "0", "100"),
            ),
        )

    def _proposal_with_hash(self, payload_hash: object) -> JournalProposal:
        """Construct a proposal while varying only payload-hash runtime type."""
        return JournalProposal(
            proposal_id=self.proposal.proposal_id,
            proposal_contract_version=self.proposal.proposal_contract_version,
            idempotency_key=self.proposal.idempotency_key,
            tenant_reference=self.proposal.tenant_reference,
            legal_entity_reference=self.proposal.legal_entity_reference,
            intended_book_role_code=self.proposal.intended_book_role_code,
            transaction_currency=self.proposal.transaction_currency,
            transaction_date=self.proposal.transaction_date,
            accounting_date=self.proposal.accounting_date,
            source_payload_hash=payload_hash,  # type: ignore[arg-type]
            source_event_references=self.proposal.source_event_references,
            lines=self.proposal.lines,
        )

    def test_constructor_rejects_non_string_payload_hash(self) -> None:
        """Malformed runtime types must fail through repository-owned validation."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "source_payload_hash must be canonical sha256",
        ):
            self._proposal_with_hash(7)

    def test_constructor_rejects_string_subclass_payload_hash(self) -> None:
        """Hash evidence must not retain caller-defined string behavior."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "source_payload_hash must be canonical sha256",
        ):
            self._proposal_with_hash(_HostilePayloadHash(self.canonical_hash))

    def test_post_revalidates_mutated_hash_before_first_retention(self) -> None:
        """A mutated hash subclass must not become retained posting provenance."""
        object.__setattr__(
            self.proposal,
            "source_payload_hash",
            _HostilePayloadHash(self.canonical_hash),
        )
        ledger = PostingLedger()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "source_payload_hash must be canonical sha256",
        ):
            ledger.post(self.proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_rejects_mutated_hash_before_cached_replay_comparison(self) -> None:
        """Cached replay must not evaluate caller-defined hash equality semantics."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        object.__setattr__(
            self.proposal,
            "source_payload_hash",
            _HostilePayloadHash(self.canonical_hash),
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "source_payload_hash must be canonical sha256",
        ):
            ledger.post(self.proposal, self.policy)

        self.assertEqual(first.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)

    def test_exact_builtin_canonical_hash_preserves_replay(self) -> None:
        """Canonical built-in hashes retain existing idempotent posting behavior."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        second = ledger.post(self.proposal, self.policy)

        self.assertEqual(first, second)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
