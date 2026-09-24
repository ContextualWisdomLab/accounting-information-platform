"""Public constructor coverage for journal proposal identity admission."""

from __future__ import annotations

from datetime import date
import unittest

from accounting_information_platform import (
    AccountingValidationError,
    JournalLineProposal,
    JournalProposal,
)


class _ProposalId(str):
    """Represent caller-defined string behavior at the proposal identity boundary."""


class JournalProposalIdentityBoundaryTests(unittest.TestCase):
    """Exercise proposal identity rejection through the public domain object."""

    @staticmethod
    def _proposal(proposal_id: object) -> JournalProposal:
        """Construct an otherwise-canonical proposal while varying proposal identity."""
        lines = (
            JournalLineProposal(1, "accounts_receivable", "100", "0"),
            JournalLineProposal(2, "usage_revenue", "0", "100"),
        )
        return JournalProposal(
            proposal_id=proposal_id,  # type: ignore[arg-type]
            proposal_contract_version=1,
            idempotency_key="invoice-proposal-identity-v1",
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 31),
            accounting_date=date(2026, 8, 31),
            source_payload_hash="sha256:" + "a" * 64,
            source_event_references=("urn:cwl:billing:event_001",),
            lines=lines,
        )

    def test_empty_proposal_id_fails_before_posting_authority(self) -> None:
        """A proposal without its immutable identity cannot be constructed."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal identity and contract version are required",
        ):
            self._proposal("")

    def test_truthy_non_string_proposal_id_fails_in_domain_validation(self) -> None:
        """A truthy non-string identity must not escape into regex TypeError."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal identity and contract version are required",
        ):
            self._proposal(123)

    def test_string_subclass_proposal_id_fails_before_regex_admission(self) -> None:
        """Caller-defined string subclasses are not canonical proposal identities."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal identity and contract version are required",
        ):
            self._proposal(_ProposalId("00000000-0000-4000-8000-000000000001"))

    def test_builtin_uuid_proposal_id_remains_valid(self) -> None:
        """A canonical built-in UUID identity remains accepted."""
        proposal_id = "00000000-0000-4000-8000-000000000001"

        proposal = self._proposal(proposal_id)

        self.assertEqual(proposal.proposal_id, proposal_id)
        self.assertIs(type(proposal.proposal_id), str)


if __name__ == "__main__":
    unittest.main()
