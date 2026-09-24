"""Regression tests for exact calendar-date admission at posting."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class PostingDateRuntimeDomainTests(unittest.TestCase):
    """Require exact calendar dates before proposal posting or replay."""

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
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf704",
            proposal_contract_version=1,
            idempotency_key="posting-date-runtime-domain-v1",
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "a" * 64,
            source_event_references=("urn:cwl:billing:invoice:704",),
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

    def test_constructor_rejects_non_date_transaction_date(self) -> None:
        proposal = self._proposal()

        with self.assertRaisesRegex(AccountingValidationError, "transaction_date must be an exact calendar date"):
            JournalProposal(
                proposal_id=proposal.proposal_id,
                proposal_contract_version=proposal.proposal_contract_version,
                idempotency_key=proposal.idempotency_key,
                tenant_reference=proposal.tenant_reference,
                legal_entity_reference=proposal.legal_entity_reference,
                intended_book_role_code=proposal.intended_book_role_code,
                transaction_currency=proposal.transaction_currency,
                transaction_date="2026-08-20",  # type: ignore[arg-type]
                accounting_date=proposal.accounting_date,
                source_payload_hash=proposal.source_payload_hash,
                source_event_references=proposal.source_event_references,
                lines=proposal.lines,
            )

    def test_constructor_rejects_datetime_accounting_date(self) -> None:
        proposal = self._proposal()

        with self.assertRaisesRegex(AccountingValidationError, "accounting_date must be an exact calendar date"):
            JournalProposal(
                proposal_id=proposal.proposal_id,
                proposal_contract_version=proposal.proposal_contract_version,
                idempotency_key=proposal.idempotency_key,
                tenant_reference=proposal.tenant_reference,
                legal_entity_reference=proposal.legal_entity_reference,
                intended_book_role_code=proposal.intended_book_role_code,
                transaction_currency=proposal.transaction_currency,
                transaction_date=proposal.transaction_date,
                accounting_date=datetime(2026, 8, 20, 12, 0),
                source_payload_hash=proposal.source_payload_hash,
                source_event_references=proposal.source_event_references,
                lines=proposal.lines,
            )

    def test_post_rejects_transaction_date_mutated_after_validation(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal, "transaction_date", "2026-08-20")
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "transaction_date must be an exact calendar date"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_post_rejects_datetime_accounting_date_before_policy_comparison(self) -> None:
        proposal = self._proposal()
        object.__setattr__(proposal, "accounting_date", datetime(2026, 8, 20, 12, 0))
        ledger = PostingLedger()

        with self.assertRaisesRegex(AccountingValidationError, "accounting_date must be an exact calendar date"):
            ledger.post(proposal, self.policy)

        self.assertEqual(ledger.journal_count, 0)

    def test_replay_rejects_invalid_date_before_cached_receipt(self) -> None:
        proposal = self._proposal()
        ledger = PostingLedger()
        original_receipt = ledger.post(proposal, self.policy)
        object.__setattr__(proposal, "accounting_date", "2026-08-20")

        with self.assertRaisesRegex(AccountingValidationError, "accounting_date must be an exact calendar date"):
            ledger.post(proposal, self.policy)

        self.assertEqual(original_receipt.posting_status_code, "posted")
        self.assertEqual(ledger.journal_count, 1)

    def test_post_accepts_exact_calendar_dates(self) -> None:
        ledger = PostingLedger()

        receipt = ledger.post(self._proposal(), self.policy)

        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
