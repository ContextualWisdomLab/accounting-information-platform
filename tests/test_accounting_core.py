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
    PostingReceipt,
    load_accounting_policy,
    load_chart_account_mapping,
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

    def test_same_idempotency_key_does_not_leak_across_tenants(self) -> None:
        """Two tenants may reuse one idempotency_key string; each keeps its receipt."""
        first = self.ledger.post(self._invoice_proposal(), self.policy)
        other_policy = self._other_tenant_policy()
        second = self.ledger.post(
            self._invoice_proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf621",
                idempotency_key=self._invoice_proposal().idempotency_key,
                tenant_reference=other_policy.tenant_reference,
                legal_entity_reference=other_policy.legal_entity_reference,
                source_payload_hash="sha256:" + "f" * 64,
            ),
            other_policy,
        )

        self.assertEqual(first.tenant_reference, self.policy.tenant_reference)
        self.assertEqual(second.tenant_reference, other_policy.tenant_reference)
        self.assertNotEqual(first, second)
        self.assertEqual(self.ledger.journal_count, 2)
        replay = self.ledger.post(self._invoice_proposal(), self.policy)
        self.assertEqual(replay, first)
        self.assertEqual(self.ledger.journal_count, 2)

    def test_duplicate_proposal_id_with_different_key_fails_closed(self) -> None:
        """A posted proposal_id cannot be overwritten by a later idempotency_key."""
        original = self._invoice_proposal()
        first = self.ledger.post(original, self.policy)
        colliding = self._invoice_proposal(
            idempotency_key="invoice-1-issued-v2",
            source_payload_hash="sha256:" + "b" * 64,
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "posted journal is immutable",
        ):
            self.ledger.post(colliding, self.policy)

        replay = self.ledger.post(original, self.policy)
        self.assertEqual(replay, first)
        self.assertEqual(self.ledger.journal_count, 1)

    def test_proposal_id_must_be_a_hyphenated_uuid(self) -> None:
        """Commercial proposal_id cannot construct a reversal journal_reference."""
        original_id = "019d7b92-1aa0-7a7f-b61c-962c0f4bf611"
        for proposal_id in (
            f"{original_id}:reversal",
            "not-a-uuid",
            original_id.replace("-", ""),
        ):
            with self.subTest(proposal_id=proposal_id):
                with self.assertRaisesRegex(
                    AccountingValidationError, "proposal_id must be a UUID"
                ):
                    self._invoice_proposal(proposal_id=proposal_id)

    def test_reverse_fails_closed_when_reversal_reference_is_occupied(self) -> None:
        """A posted journal at the reversal reference is not overwritten."""
        first = self.ledger.post(self._invoice_proposal(), self.policy)
        colliding = self.ledger.post(
            self._invoice_proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf699",
                idempotency_key="invoice-reversal-collision-v1",
                source_payload_hash="sha256:" + "e" * 64,
            ),
            self.policy,
        )
        colliding_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference, colliding.journal_reference
        )
        occupant = self.ledger._journals.pop(colliding_key)
        reversal_reference = f"{first.journal_reference}:reversal"
        reversal_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference, reversal_reference
        )
        self.ledger._journals[reversal_key] = occupant

        with self.assertRaisesRegex(
            AccountingValidationError, "posted journal is immutable"
        ):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
            )

        self.assertIs(self.ledger._journals[reversal_key], occupant)
        self.assertEqual(self.ledger.journal_count, 2)
        self.assertEqual(
            self.ledger._journals[
                self.ledger._tenant_cache_key(
                    self.policy.tenant_reference, first.journal_reference
                )
            ].source_proposal_id,
            first.source_proposal_id,
        )

    def test_reverse_replays_existing_reversal_when_receipt_cache_is_missing(self) -> None:
        """Idempotent reverse still returns the original reversing journal."""
        first = self.ledger.post(self._invoice_proposal(), self.policy)
        reversal = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        del self.ledger._reversal_receipts[
            self.ledger._tenant_cache_key(
                self.policy.tenant_reference, first.journal_reference
            )
        ]
        stored = self.ledger._journals[
            self.ledger._tenant_cache_key(
                self.policy.tenant_reference, reversal.journal_reference
            )
        ]

        replay = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )

        self.assertEqual(replay, reversal)
        self.assertIs(
            self.ledger._journals[
                self.ledger._tenant_cache_key(
                    self.policy.tenant_reference, reversal.journal_reference
                )
            ],
            stored,
        )
        self.assertEqual(self.ledger.journal_count, 2)

    def test_same_proposal_id_posts_independently_per_tenant(self) -> None:
        """journal_reference identity is tenant-scoped, matching PostgreSQL uniqueness."""
        first = self.ledger.post(self._invoice_proposal(), self.policy)
        other_policy = self._other_tenant_policy()
        second = self.ledger.post(
            self._invoice_proposal(
                tenant_reference=other_policy.tenant_reference,
                legal_entity_reference=other_policy.legal_entity_reference,
                idempotency_key="invoice-tenant-2-same-proposal",
                source_payload_hash="sha256:" + "c" * 64,
            ),
            other_policy,
        )

        self.assertEqual(first.journal_reference, second.journal_reference)
        self.assertNotEqual(first.tenant_reference, second.tenant_reference)
        self.assertEqual(self.ledger.journal_count, 2)

    def test_reversal_cache_does_not_leak_across_tenants(self) -> None:
        """A reversal receipt is stored and returned only for its tenant."""
        first = self.ledger.post(self._invoice_proposal(), self.policy)
        first_reversal = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        other_policy = self._other_tenant_policy()
        second = self.ledger.post(
            self._invoice_proposal(
                tenant_reference=other_policy.tenant_reference,
                legal_entity_reference=other_policy.legal_entity_reference,
                idempotency_key="invoice-tenant-2-reversal",
                source_payload_hash="sha256:" + "d" * 64,
            ),
            other_policy,
        )
        second_reversal = self.ledger.reverse(
            second.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            other_policy,
        )

        self.assertEqual(first.journal_reference, second.journal_reference)
        self.assertNotEqual(first_reversal, second_reversal)
        self.assertEqual(first_reversal.tenant_reference, self.policy.tenant_reference)
        self.assertEqual(second_reversal.tenant_reference, other_policy.tenant_reference)
        self.assertEqual(self.ledger.journal_count, 4)
        replay = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        self.assertEqual(replay, first_reversal)
        self.assertEqual(self.ledger.journal_count, 4)

    def test_cache_hits_require_stored_tenant_match(self) -> None:
        """A cache row stored under one tenant is not returned for another tenant value."""
        original = self._invoice_proposal()
        first = self.ledger.post(original, self.policy)
        foreign_receipt = PostingReceipt(
            receipt_reference=first.receipt_reference,
            journal_reference=first.journal_reference,
            posting_status_code=first.posting_status_code,
            source_proposal_id=first.source_proposal_id,
            source_payload_hash=first.source_payload_hash,
            tenant_reference="urn:cwl:tenant_002",
            legal_entity_reference=first.legal_entity_reference,
            accounting_book_reference=first.accounting_book_reference,
            accounting_policy_version=first.accounting_policy_version,
            posting_rule_version=first.posting_rule_version,
            line_count=first.line_count,
        )
        idempotency_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference,
            original.idempotency_key,
        )
        self.ledger._receipts_by_idempotency[idempotency_key] = (
            original.source_payload_hash,
            foreign_receipt,
        )
        with self.assertRaisesRegex(AccountingValidationError, "posted journal is immutable"):
            self.ledger.post(original, self.policy)

        reversal = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        reversal_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference,
            first.journal_reference,
        )
        self.ledger._reversal_receipts[reversal_key] = PostingReceipt(
            receipt_reference=reversal.receipt_reference,
            journal_reference=reversal.journal_reference,
            posting_status_code=reversal.posting_status_code,
            source_proposal_id=reversal.source_proposal_id,
            source_payload_hash=reversal.source_payload_hash,
            tenant_reference="urn:cwl:tenant_002",
            legal_entity_reference=reversal.legal_entity_reference,
            accounting_book_reference=reversal.accounting_book_reference,
            accounting_policy_version=reversal.accounting_policy_version,
            posting_rule_version=reversal.posting_rule_version,
            line_count=reversal.line_count,
            reversal_of_journal_reference=reversal.reversal_of_journal_reference,
        )
        replay_reversal = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        self.assertEqual(replay_reversal.tenant_reference, self.policy.tenant_reference)
        self.assertNotEqual(replay_reversal.tenant_reference, "urn:cwl:tenant_002")

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
            (1, "accounts_receivable", "0.0000010", "0"),
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

    def test_duplicate_account_role_mapping_fails_closed_before_policy_load(self) -> None:
        """A role may map to only one chart account before AccountingPolicy is built."""
        catalog = self._catalog_account_mappings()
        duplicate_role = (
            *catalog,
            {
                "account_role_code": "accounts_receivable",
                "chart_account_code": "119900",
            },
        )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "account role accounts_receivable is mapped more than once",
        ):
            load_chart_account_mapping(duplicate_role)
        with self.assertRaisesRegex(
            AccountingValidationError,
            "account role accounts_receivable is mapped more than once",
        ):
            load_accounting_policy(self._policy_manifest(account_mappings=duplicate_role))
        identical_duplicate = (
            *catalog,
            {
                "account_role_code": "tax_payable",
                "chart_account_code": "210100",
            },
        )
        with self.assertRaisesRegex(
            AccountingValidationError, "tax_payable is mapped more than once"
        ):
            load_chart_account_mapping(identical_duplicate)
        with self.assertRaisesRegex(AccountingValidationError, "at least one role"):
            load_chart_account_mapping(())
        with self.assertRaisesRegex(AccountingValidationError, "must be an object"):
            load_chart_account_mapping(("not-a-mapping",))  # type: ignore[arg-type]
        with self.assertRaisesRegex(AccountingValidationError, "must include account_role_code"):
            load_chart_account_mapping(({"account_role_code": "accounts_receivable"},))
        with self.assertRaisesRegex(AccountingValidationError, "must include account_role_code"):
            load_chart_account_mapping(({"chart_account_code": "110100"},))
        with self.assertRaisesRegex(AccountingValidationError, "must be an array"):
            load_accounting_policy(self._policy_manifest(account_mappings="not-an-array"))
        with self.assertRaisesRegex(AccountingValidationError, "must be an array"):
            load_accounting_policy(self._policy_manifest(account_mappings=b"not-an-array"))
        with self.assertRaisesRegex(AccountingValidationError, "must be an array"):
            load_accounting_policy(self._policy_manifest(account_mappings=None))
        with self.assertRaisesRegex(AccountingValidationError, "must be an ISO date"):
            load_accounting_policy(self._policy_manifest(open_period_start="31-08-2026"))

    def test_catalog_account_mappings_load_once_per_role(self) -> None:
        """The seven catalog roles load uniquely and invent no withholding role."""
        catalog = self._catalog_account_mappings()
        mapping = load_chart_account_mapping(catalog)
        policy = load_accounting_policy(self._policy_manifest(account_mappings=catalog))
        expected = {
            "accounts_receivable": "110100",
            "cash_receipt": "110200",
            "tax_payable": "210100",
            "unapplied_cash": "210200",
            "retained_earnings": "310100",
            "usage_revenue": "410100",
            "write_off_expense": "510100",
        }
        self.assertEqual(mapping, expected)
        self.assertEqual(dict(policy.chart_account_mapping), expected)
        self.assertNotIn("wage_withholding", mapping)
        self.assertNotIn("year_end_settlement", mapping)
        self.assertNotIn("payroll_withholding", mapping)

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
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference="urn:cwl:legal_entity:entity_999",
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
        other_tenant_policy = self._other_tenant_policy()
        with self.assertRaisesRegex(AccountingValidationError, "does not exist"):
            self.ledger.reverse(
                receipt.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                other_tenant_policy,
            )
        wrong_book_policy = AccountingPolicy(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference="urn:cwl:accounting_book:other_statutory",
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
                wrong_book_policy,
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

    def _other_tenant_policy(self) -> AccountingPolicy:
        return AccountingPolicy(
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

    def _catalog_account_mappings(self) -> tuple[dict[str, str], ...]:
        return (
            {"account_role_code": "accounts_receivable", "chart_account_code": "110100"},
            {"account_role_code": "cash_receipt", "chart_account_code": "110200"},
            {"account_role_code": "tax_payable", "chart_account_code": "210100"},
            {"account_role_code": "unapplied_cash", "chart_account_code": "210200"},
            {"account_role_code": "retained_earnings", "chart_account_code": "310100"},
            {"account_role_code": "usage_revenue", "chart_account_code": "410100"},
            {"account_role_code": "write_off_expense", "chart_account_code": "510100"},
        )

    def _policy_manifest(self, **overrides: object) -> dict[str, object]:
        manifest: dict[str, object] = {
            "policy_manifest_id": "019d7b92-1aa0-7a7f-b61c-962c0f4bf612",
            "policy_contract_version": 1,
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "accounting_book_reference": self.policy.accounting_book_reference,
            "intended_book_role_code": self.policy.intended_book_role_code,
            "transaction_currency": "KRW",
            "functional_currency": "KRW",
            "open_period_start": "2026-08-01",
            "open_period_end": "2026-08-31",
            "accounting_policy_version": "ifrs-v1",
            "posting_rule_version": "billing-issued-v1",
            "account_mappings": list(self._catalog_account_mappings()),
        }
        manifest.update(overrides)
        return manifest


if __name__ == "__main__":
    unittest.main()
