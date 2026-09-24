"""Regression tests for opaque accounting-reference runtime admission."""

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


class _HostileReference(str):
    """Expose caller-defined hashing/equality if a reference reaches cache control."""

    def __hash__(self) -> int:
        """Fail if tenant-scoped cache identity hashes caller-owned behavior."""
        raise AssertionError("caller-defined reference hashing executed")

    def __eq__(self, other: object) -> bool:
        """Fail if retained/control equality reaches caller-owned behavior."""
        raise AssertionError("caller-defined reference equality executed")

    def __ne__(self, other: object) -> bool:
        """Fail if retained/control inequality reaches caller-owned behavior."""
        raise AssertionError("caller-defined reference inequality executed")


class ReferenceRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in strings for opaque CWL accounting references."""

    def setUp(self) -> None:
        """Prepare one canonical proposal and policy for posting and replay."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_reference_runtime",
            legal_entity_reference="urn:cwl:legal_entity:reference_runtime",
            accounting_book_reference="urn:cwl:accounting_book:reference_runtime",
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
            posting_rule_version="reference-runtime-v1",
        )
        self.proposal = JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf821",
            proposal_contract_version=1,
            idempotency_key="reference-runtime-command-v1",
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "e" * 64,
            source_event_references=("urn:cwl:billing:invoice:reference_runtime",),
            lines=(
                JournalLineProposal(1, "accounts_receivable", "100", "0"),
                JournalLineProposal(2, "usage_revenue", "0", "100"),
            ),
        )

    def _proposal_with_tenant_reference(self, tenant_reference: object) -> JournalProposal:
        """Construct a proposal while varying only tenant-reference runtime type."""
        return JournalProposal(
            proposal_id=self.proposal.proposal_id,
            proposal_contract_version=self.proposal.proposal_contract_version,
            idempotency_key=self.proposal.idempotency_key,
            tenant_reference=tenant_reference,  # type: ignore[arg-type]
            legal_entity_reference=self.proposal.legal_entity_reference,
            intended_book_role_code=self.proposal.intended_book_role_code,
            transaction_currency=self.proposal.transaction_currency,
            transaction_date=self.proposal.transaction_date,
            accounting_date=self.proposal.accounting_date,
            source_payload_hash=self.proposal.source_payload_hash,
            source_event_references=self.proposal.source_event_references,
            lines=self.proposal.lines,
        )

    def _policy_copy(self) -> AccountingPolicy:
        """Return a fresh canonical policy so each mutation scenario is isolated."""
        return AccountingPolicy(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency=self.policy.transaction_currency,
            functional_currency=self.policy.functional_currency,
            open_period_start=self.policy.open_period_start,
            open_period_end=self.policy.open_period_end,
            chart_account_mapping=dict(self.policy.chart_account_mapping),
            accounting_policy_version=self.policy.accounting_policy_version,
            posting_rule_version=self.policy.posting_rule_version,
        )

    def test_constructor_rejects_non_string_tenant_reference(self) -> None:
        """Non-string tenant identity must fail through repository validation."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "tenant reference must be a CWL URN",
        ):
            self._proposal_with_tenant_reference(7)

    def test_constructor_rejects_string_subclass_tenant_reference(self) -> None:
        """Tenant identity must not retain caller-defined string behavior."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "tenant reference must be a CWL URN",
        ):
            self._proposal_with_tenant_reference(
                _HostileReference(self.proposal.tenant_reference)
            )

    def test_constructor_rejects_string_subclass_source_event_reference(self) -> None:
        """Source provenance references use the same exact opaque-reference domain."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "source event reference must be a CWL URN",
        ):
            JournalProposal(
                proposal_id=self.proposal.proposal_id,
                proposal_contract_version=self.proposal.proposal_contract_version,
                idempotency_key=self.proposal.idempotency_key,
                tenant_reference=self.proposal.tenant_reference,
                legal_entity_reference=self.proposal.legal_entity_reference,
                intended_book_role_code=self.proposal.intended_book_role_code,
                transaction_currency=self.proposal.transaction_currency,
                transaction_date=self.proposal.transaction_date,
                accounting_date=self.proposal.accounting_date,
                source_payload_hash=self.proposal.source_payload_hash,
                source_event_references=(
                    _HostileReference(self.proposal.source_event_references[0]),
                ),
                lines=self.proposal.lines,
            )

    def test_post_revalidates_mutated_tenant_reference_before_cache_hash(self) -> None:
        """Mutation before first post must fail before tenant-scoped dictionary hashing."""
        object.__setattr__(
            self.proposal,
            "tenant_reference",
            _HostileReference(self.proposal.tenant_reference),
        )
        ledger = PostingLedger()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "tenant reference must be a CWL URN",
        ):
            ledger.post(self.proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_rejects_mutated_tenant_reference_before_cached_replay(self) -> None:
        """Cached replay must not hash or compare a caller-owned tenant subclass."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        object.__setattr__(
            self.proposal,
            "tenant_reference",
            _HostileReference(self.proposal.tenant_reference),
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "tenant reference must be a CWL URN",
        ):
            ledger.post(self.proposal, self.policy)

        self.assertEqual(first.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)

    def test_post_revalidates_current_policy_references_before_persistence(self) -> None:
        """Posting must reject mutated policy identity before it becomes retained evidence."""
        fields = (
            ("tenant_reference", "tenant reference"),
            ("legal_entity_reference", "legal entity reference"),
            ("accounting_book_reference", "accounting book reference"),
        )
        for field_name, label in fields:
            with self.subTest(field_name=field_name):
                policy = self._policy_copy()
                original = getattr(policy, field_name)
                object.__setattr__(policy, field_name, _HostileReference(original))
                ledger = PostingLedger()

                with self.assertRaisesRegex(
                    AccountingValidationError,
                    f"{label} must be a CWL URN",
                ):
                    ledger.post(self.proposal, policy)

                self.assertEqual(ledger.journal_count, 0)

    def test_post_revalidates_current_policy_references_before_cached_replay(self) -> None:
        """A retained receipt must not hide mutation of current policy scope identity."""
        fields = (
            ("tenant_reference", "tenant reference"),
            ("legal_entity_reference", "legal entity reference"),
            ("accounting_book_reference", "accounting book reference"),
        )
        for field_name, label in fields:
            with self.subTest(field_name=field_name):
                ledger = PostingLedger()
                first = ledger.post(self.proposal, self.policy)
                policy = self._policy_copy()
                original = getattr(policy, field_name)
                object.__setattr__(policy, field_name, _HostileReference(original))

                with self.assertRaisesRegex(
                    AccountingValidationError,
                    f"{label} must be a CWL URN",
                ):
                    ledger.post(self.proposal, policy)

                self.assertEqual(first.line_count, 2)
                self.assertEqual(ledger.journal_count, 1)

    def test_reverse_revalidates_current_policy_references_before_hash_or_cache(self) -> None:
        """Reversal policy identity must be owned before hashing or tenant cache access."""
        fields = (
            ("tenant_reference", "tenant reference"),
            ("legal_entity_reference", "legal entity reference"),
            ("accounting_book_reference", "accounting book reference"),
        )
        for field_name, label in fields:
            with self.subTest(field_name=field_name):
                ledger = PostingLedger()
                posted = ledger.post(self.proposal, self.policy)
                policy = self._policy_copy()
                original = getattr(policy, field_name)
                object.__setattr__(policy, field_name, _HostileReference(original))

                with self.assertRaisesRegex(
                    AccountingValidationError,
                    f"{label} must be a CWL URN",
                ):
                    ledger.reverse(
                        posted.journal_reference,
                        date(2026, 8, 21),
                        "policy_reference_runtime",
                        policy,
                        reversal_idempotency_key="policy-reference-runtime-reversal",
                    )

                self.assertEqual(ledger.journal_count, 1)

    def test_exact_builtin_cwl_references_preserve_replay(self) -> None:
        """Canonical built-in reference strings preserve posting idempotency."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        second = ledger.post(self.proposal, self.policy)

        self.assertEqual(first, second)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
