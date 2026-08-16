"""Behavioral tests for the accounting posting reference core."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class AccountingCoreTests(unittest.TestCase):
    """Exercise the source-to-posting vertical and its invariants."""

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
                "accounts_receivable": "110100",
                "usage_revenue": "410100",
                "tax_payable": "210300",
                "cash_clearing": "110900",
            },
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )
        self.ledger = PostingLedger()

    def test_posts_balanced_proposal_and_produces_trial_balance(self) -> None:
        proposal = self._invoice_proposal()

        receipt = self.ledger.post(proposal, self.policy)
        balances = self.ledger.trial_balance(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            through_date=date(2026, 8, 31),
        )

        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(receipt.source_proposal_id, proposal.proposal_id)
        self.assertEqual(receipt.source_payload_hash, proposal.source_payload_hash)
        self.assertEqual(receipt.line_count, 3)
        self.assertEqual(balances["110100"].debit_total, Decimal("110000"))
        self.assertEqual(balances["410100"].credit_total, Decimal("100000"))
        self.assertEqual(balances["210300"].credit_total, Decimal("10000"))
        self.assertEqual(
            sum(balance.debit_total for balance in balances.values()),
            sum(balance.credit_total for balance in balances.values()),
        )

    def test_replay_returns_same_receipt_without_duplicate_posting(self) -> None:
        proposal = self._invoice_proposal()

        first = self.ledger.post(proposal, self.policy)
        second = self.ledger.post(proposal, self.policy)

        self.assertEqual(first, second)
        self.assertEqual(self.ledger.journal_count, 1)

    def test_same_idempotency_key_with_different_payload_fails_closed(self) -> None:
        proposal = self._invoice_proposal()
        self.ledger.post(proposal, self.policy)
        changed = JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf612",
            proposal_contract_version=1,
            idempotency_key=proposal.idempotency_key,
            tenant_reference=proposal.tenant_reference,
            legal_entity_reference=proposal.legal_entity_reference,
            intended_book_role_code=proposal.intended_book_role_code,
            transaction_currency=proposal.transaction_currency,
            transaction_date=proposal.transaction_date,
            accounting_date=proposal.accounting_date,
            source_payload_hash="sha256:" + "b" * 64,
            source_event_references=proposal.source_event_references,
            lines=proposal.lines,
        )

        with self.assertRaises(IdempotencyConflictError):
            self.ledger.post(changed, self.policy)

    def test_unbalanced_proposal_is_rejected(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "must balance"):
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf613",
                proposal_contract_version=1,
                idempotency_key="invoice-2",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 31),
                accounting_date=date(2026, 8, 31),
                source_payload_hash="sha256:" + "c" * 64,
                source_event_references=("urn:cwl:billing:invoice:2",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "99"),
                ),
            )

    def test_line_requires_one_positive_side(self) -> None:
        for debit_amount, credit_amount in (("0", "0"), ("1", "1")):
            with self.subTest(debit=debit_amount, credit=credit_amount):
                with self.assertRaises(AccountingValidationError):
                    JournalLineProposal(
                        1,
                        "accounts_receivable",
                        debit_amount,
                        credit_amount,
                    )

    def test_duplicate_line_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "line numbers"):
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf614",
                proposal_contract_version=1,
                idempotency_key="invoice-3",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 31),
                accounting_date=date(2026, 8, 31),
                source_payload_hash="sha256:" + "d" * 64,
                source_event_references=("urn:cwl:billing:invoice:3",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(1, "usage_revenue", "0", "100"),
                ),
            )

    def test_closed_period_is_rejected(self) -> None:
        proposal = self._invoice_proposal(accounting_date=date(2026, 9, 1))

        with self.assertRaisesRegex(AccountingValidationError, "closed fiscal period"):
            self.ledger.post(proposal, self.policy)

    def test_policy_scope_mismatch_is_rejected(self) -> None:
        proposal = self._invoice_proposal(tenant_reference="urn:cwl:tenant_002")

        with self.assertRaisesRegex(AccountingValidationError, "tenant scope"):
            self.ledger.post(proposal, self.policy)

    def test_missing_account_mapping_is_rejected(self) -> None:
        policy = AccountingPolicy(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping={"accounts_receivable": "110100"},
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )

        with self.assertRaisesRegex(AccountingValidationError, "unmapped account role"):
            self.ledger.post(self._invoice_proposal(), policy)

    def test_unsupported_currency_conversion_is_rejected(self) -> None:
        policy = AccountingPolicy(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="USD",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping=self.policy.chart_account_mapping,
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )
        proposal = self._invoice_proposal(transaction_currency="USD")

        with self.assertRaisesRegex(AccountingValidationError, "foreign exchange"):
            self.ledger.post(proposal, policy)

    def test_reversal_preserves_history_and_zeroes_net_balance(self) -> None:
        receipt = self.ledger.post(self._invoice_proposal(), self.policy)

        reversal = self.ledger.reverse(
            journal_reference=receipt.journal_reference,
            reversal_date=date(2026, 8, 31),
            reversal_reason_code="billing_correction",
            policy=self.policy,
        )
        balances = self.ledger.trial_balance(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            through_date=date(2026, 8, 31),
        )

        self.assertEqual(reversal.posting_status_code, "posted")
        self.assertEqual(reversal.reversal_of_journal_reference, receipt.journal_reference)
        self.assertEqual(self.ledger.journal_count, 2)
        for balance in balances.values():
            self.assertEqual(balance.net_balance, Decimal("0"))

    def test_second_reversal_returns_existing_receipt(self) -> None:
        receipt = self.ledger.post(self._invoice_proposal(), self.policy)
        first = self.ledger.reverse(
            receipt.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        second = self.ledger.reverse(
            receipt.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )

        self.assertEqual(first, second)
        self.assertEqual(self.ledger.journal_count, 2)

    def test_unknown_or_reversal_journal_cannot_be_reversed(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "does not exist"):
            self.ledger.reverse(
                "urn:cwl:journal:missing",
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
            )

        original = self.ledger.post(self._invoice_proposal(), self.policy)
        reversal = self.ledger.reverse(
            original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        with self.assertRaisesRegex(AccountingValidationError, "reversal journal"):
            self.ledger.reverse(
                reversal.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
            )

    def test_trial_balance_filters_scope_and_date(self) -> None:
        self.ledger.post(self._invoice_proposal(), self.policy)
        other_policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_002",
            legal_entity_reference="urn:cwl:legal_entity:entity_002",
            accounting_book_reference="urn:cwl:accounting_book:primary_statutory_2",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping=self.policy.chart_account_mapping,
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )
        self.ledger.post(
            self._invoice_proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf615",
                idempotency_key="invoice-tenant-2",
                tenant_reference=other_policy.tenant_reference,
                legal_entity_reference=other_policy.legal_entity_reference,
                source_payload_hash="sha256:" + "e" * 64,
            ),
            other_policy,
        )

        before = self.ledger.trial_balance(
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            date(2026, 8, 30),
        )
        current = self.ledger.trial_balance(
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            date(2026, 8, 31),
        )

        self.assertEqual(before, {})
        self.assertEqual(len(current), 3)

    def test_value_contracts_reject_invalid_identifiers_hashes_and_amounts(self) -> None:
        invalid_line_inputs = (
            (0, "accounts_receivable", "1", "0"),
            (1, "Accounts Receivable", "1", "0"),
            (1, "accounts_receivable", "-1", "0"),
            (1, "accounts_receivable", "01", "0"),
            (1, "accounts_receivable", "1.1234567", "0"),
        )
        for values in invalid_line_inputs:
            with self.subTest(values=values):
                with self.assertRaises(AccountingValidationError):
                    JournalLineProposal(*values)

        with self.assertRaises(AccountingValidationError):
            self._invoice_proposal(source_payload_hash="not-a-hash")
        with self.assertRaises(AccountingValidationError):
            self._invoice_proposal(transaction_currency="krw")
        with self.assertRaises(AccountingValidationError):
            self._invoice_proposal(source_event_references=())
        with self.assertRaises(AccountingValidationError):
            self._invoice_proposal(proposal_contract_version=0)
        with self.assertRaises(AccountingValidationError):
            AccountingPolicy(
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                accounting_book_reference=self.policy.accounting_book_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                functional_currency="KRW",
                open_period_start=date(2026, 9, 1),
                open_period_end=date(2026, 8, 1),
                chart_account_mapping=self.policy.chart_account_mapping,
                accounting_policy_version="ifrs-v1",
                posting_rule_version="billing-issued-v1",
            )

    def test_remaining_validation_and_reversal_branches_fail_closed(self) -> None:
        proposal = self._invoice_proposal()
        self.assertEqual(proposal.debit_total, Decimal("110000"))
        self.assertEqual(proposal.credit_total, Decimal("110000"))

        invalid_proposal_overrides = (
            {"idempotency_key": ""},
            {"lines": (JournalLineProposal(1, "accounts_receivable", "1", "0"),)},
            {"legal_entity_reference": "not-a-urn"},
        )
        for overrides in invalid_proposal_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(AccountingValidationError):
                    self._invoice_proposal(**overrides)

        invalid_policy_values = (
            {"accounting_policy_version": ""},
            {"chart_account_mapping": {"accounts_receivable": "bad code"}},
        )
        for replacements in invalid_policy_values:
            values = {
                "tenant_reference": self.policy.tenant_reference,
                "legal_entity_reference": self.policy.legal_entity_reference,
                "accounting_book_reference": self.policy.accounting_book_reference,
                "intended_book_role_code": self.policy.intended_book_role_code,
                "transaction_currency": "KRW",
                "functional_currency": "KRW",
                "open_period_start": date(2026, 8, 1),
                "open_period_end": date(2026, 8, 31),
                "chart_account_mapping": self.policy.chart_account_mapping,
                "accounting_policy_version": "ifrs-v1",
                "posting_rule_version": "billing-issued-v1",
            }
            values.update(replacements)
            with self.subTest(replacements=replacements):
                with self.assertRaises(AccountingValidationError):
                    AccountingPolicy(**values)

        receipt = self.ledger.post(proposal, self.policy)
        with self.assertRaisesRegex(AccountingValidationError, "closed fiscal period"):
            self.ledger.reverse(
                receipt.journal_reference,
                date(2026, 9, 1),
                "billing_correction",
                self.policy,
            )
        wrong_scope_policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_002",
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping=self.policy.chart_account_mapping,
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )
        with self.assertRaisesRegex(AccountingValidationError, "scope"):
            self.ledger.reverse(
                receipt.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                wrong_scope_policy,
            )

    def test_each_policy_scope_dimension_is_enforced(self) -> None:
        cases = (
            (
                {"legal_entity_reference": "urn:cwl:legal_entity:entity_999"},
                "legal entity",
            ),
            ({"intended_book_role_code": "management_book"}, "book role"),
            ({"transaction_currency": "USD"}, "transaction currency"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(AccountingValidationError, message):
                    self.ledger.post(self._invoice_proposal(**overrides), self.policy)

    def _invoice_proposal(self, **overrides: object) -> JournalProposal:
        values: dict[str, object] = {
            "proposal_id": "019d7b92-1aa0-7a7f-b61c-962c0f4bf611",
            "proposal_contract_version": 1,
            "idempotency_key": "invoice-1-issued-v1",
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "intended_book_role_code": self.policy.intended_book_role_code,
            "transaction_currency": "KRW",
            "transaction_date": date(2026, 8, 31),
            "accounting_date": date(2026, 8, 31),
            "source_payload_hash": "sha256:" + "a" * 64,
            "source_event_references": ("urn:cwl:billing:invoice:019d",),
            "lines": (
                JournalLineProposal(1, "accounts_receivable", "110000", "0"),
                JournalLineProposal(2, "usage_revenue", "0", "100000"),
                JournalLineProposal(3, "tax_payable", "0", "10000"),
            ),
        }
        values.update(overrides)
        return JournalProposal(**values)


if __name__ == "__main__":
    unittest.main()
