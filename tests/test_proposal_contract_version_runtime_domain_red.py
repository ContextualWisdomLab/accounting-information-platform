"""Regression tests for proposal contract-version runtime admission."""

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


class _HostileContractVersion(int):
    """Expose caller-defined ordering before repository version admission."""

    def __lt__(self, other: object) -> bool:
        """Fail if constructor validation evaluates subclass ordering."""
        raise AssertionError("caller-defined contract-version ordering executed")


class ProposalContractVersionRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in positive integers for proposal contract identity."""

    def setUp(self) -> None:
        """Prepare one canonical proposal and policy for posting/replay."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_contract_version",
            legal_entity_reference="urn:cwl:legal_entity:contract_version",
            accounting_book_reference="urn:cwl:accounting_book:contract_version",
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
            posting_rule_version="contract-version-v1",
        )
        self.proposal = JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf819",
            proposal_contract_version=1,
            idempotency_key="contract-version-command-v1",
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "c" * 64,
            source_event_references=("urn:cwl:billing:invoice:contract_version",),
            lines=(
                JournalLineProposal(1, "accounts_receivable", "100", "0"),
                JournalLineProposal(2, "usage_revenue", "0", "100"),
            ),
        )

    def _proposal_with_version(self, version: object) -> JournalProposal:
        """Construct a proposal while varying only contract-version runtime type."""
        return JournalProposal(
            proposal_id=self.proposal.proposal_id,
            proposal_contract_version=version,  # type: ignore[arg-type]
            idempotency_key=self.proposal.idempotency_key,
            tenant_reference=self.proposal.tenant_reference,
            legal_entity_reference=self.proposal.legal_entity_reference,
            intended_book_role_code=self.proposal.intended_book_role_code,
            transaction_currency=self.proposal.transaction_currency,
            transaction_date=self.proposal.transaction_date,
            accounting_date=self.proposal.accounting_date,
            source_payload_hash=self.proposal.source_payload_hash,
            source_event_references=self.proposal.source_event_references,
            lines=self.proposal.lines,
        )

    def test_constructor_rejects_bool_contract_version(self) -> None:
        """Boolean values must not become proposal contract identity."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal_contract_version must be a positive integer",
        ):
            self._proposal_with_version(True)

    def test_constructor_rejects_int_subclass_before_ordering(self) -> None:
        """Caller-defined integer comparison must not participate in admission."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal_contract_version must be a positive integer",
        ):
            self._proposal_with_version(_HostileContractVersion(1))

    def test_post_revalidates_mutated_contract_version_before_replay(self) -> None:
        """A cached receipt must not authorize a runtime-invalid current version."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        object.__setattr__(self.proposal, "proposal_contract_version", True)

        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal_contract_version must be a positive integer",
        ):
            ledger.post(self.proposal, self.policy)

        self.assertEqual(first.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)

    def test_exact_positive_builtin_integer_preserves_replay(self) -> None:
        """Canonical contract versions retain existing idempotent posting behavior."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        second = ledger.post(self.proposal, self.policy)

        self.assertEqual(first, second)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
