"""Regression tests for journal-line semantic-role runtime admission."""

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


class _HostileRoleCode(str):
    """Expose caller-owned hashing if a role escapes repository admission."""

    def __hash__(self) -> int:
        raise AssertionError("caller-owned role hashing executed")


class PostingLineRoleRuntimeDomainTests(unittest.TestCase):
    """Require exact built-in semantic-role strings before posting or replay."""

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
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf782",
            proposal_contract_version=1,
            idempotency_key="posting-line-role-runtime-v1",
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "2" * 64,
            source_event_references=("urn:cwl:billing:invoice:782",),
            lines=(
                JournalLineProposal(
                    line_number=1,
                    account_role_code="cash",
                    debit_amount=Decimal("100.00"),
                    credit_amount=Decimal("0"),
                ),
                JournalLineProposal(
                    line_number=2,
                    account_role_code="usage_revenue",
                    debit_amount=Decimal("0"),
                    credit_amount=Decimal("100.00"),
                ),
            ),
        )

    def test_line_constructor_rejects_non_string_role_through_accounting_validation(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "account role code"):
            JournalLineProposal(
                line_number=1,
                account_role_code=object(),  # type: ignore[arg-type]
                debit_amount=Decimal("100.00"),
                credit_amount=Decimal("0"),
            )

    def test_post_rejects_hostile_role_before_chart_mapping_hashing(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal.lines[0], "account_role_code", _HostileRoleCode("cash"))
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "account role code"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_replay_rejects_hostile_role_before_cached_receipt_return(self) -> None:
        proposal = self._proposal()
        ledger = PostingLedger()
        original_receipt = ledger.post(proposal, self.policy)
        object.__setattr__(proposal.lines[0], "account_role_code", _HostileRoleCode("cash"))

        with self.assertRaisesRegex(AccountingValidationError, "account role code"):
            ledger.post(proposal, self.policy)

        self.assertEqual(original_receipt.posting_status_code, "posted")
        self.assertEqual(ledger.journal_count, 1)

    def test_post_preserves_exact_builtin_role_behavior(self) -> None:
        ledger = PostingLedger()

        receipt = ledger.post(self._proposal(), self.policy)

        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(receipt.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
