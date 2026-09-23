"""Public constructor coverage for journal proposal identity admission."""

from __future__ import annotations

from datetime import date
import unittest

from accounting_information_platform import (
    AccountingValidationError,
    JournalLineProposal,
    JournalProposal,
)


class JournalProposalIdentityBoundaryTests(unittest.TestCase):
    """Exercise proposal identity rejection through the public domain object."""

    def test_empty_proposal_id_fails_before_posting_authority(self) -> None:
        """A proposal without its immutable identity cannot be constructed."""
        lines = (
            JournalLineProposal(1, "accounts_receivable", "100", "0"),
            JournalLineProposal(2, "usage_revenue", "0", "100"),
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal identity and contract version are required",
        ):
            JournalProposal(
                proposal_id="",
                proposal_contract_version=1,
                idempotency_key="invoice-empty-proposal-id-v1",
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


if __name__ == "__main__":
    unittest.main()
