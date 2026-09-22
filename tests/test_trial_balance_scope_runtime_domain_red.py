"""Regression tests for trial-balance scope-reference runtime admission."""

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


class TrialBalanceScopeRuntimeDomainRedTests(unittest.TestCase):
    """Require canonical CWL scope references before trial-balance filtering."""

    def setUp(self) -> None:
        """Create one posted journal under a canonical tenant/entity/book scope."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_trial_balance_scope",
            legal_entity_reference="urn:cwl:legal_entity:trial_balance_scope",
            accounting_book_reference="urn:cwl:accounting_book:trial_balance_scope",
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
            posting_rule_version="trial-balance-scope-v1",
        )
        self.ledger = PostingLedger()
        self.ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf814",
                proposal_contract_version=1,
                idempotency_key="trial-balance-scope-original-v1",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
                source_payload_hash="sha256:" + "d" * 64,
                source_event_references=("urn:cwl:billing:invoice:trial_balance_scope",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            self.policy,
        )

    def test_trial_balance_rejects_malformed_scope_references(self) -> None:
        """Malformed tenant, entity, and book identities must not become empty reads."""
        cases = (
            ("tenant_reference", "tenant_trial_balance_scope", "tenant reference must be a CWL URN"),
            (
                "legal_entity_reference",
                "legal_entity_trial_balance_scope",
                "legal entity reference must be a CWL URN",
            ),
            (
                "accounting_book_reference",
                "accounting_book_trial_balance_scope",
                "accounting book reference must be a CWL URN",
            ),
        )
        canonical = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "accounting_book_reference": self.policy.accounting_book_reference,
        }
        for field_name, invalid_reference, message in cases:
            with self.subTest(field_name=field_name):
                scope = dict(canonical)
                scope[field_name] = invalid_reference
                with self.assertRaisesRegex(AccountingValidationError, message):
                    self.ledger.trial_balance(
                        tenant_reference=scope["tenant_reference"],
                        legal_entity_reference=scope["legal_entity_reference"],
                        accounting_book_reference=scope["accounting_book_reference"],
                        through_date=date(2026, 8, 31),
                    )

    def test_canonical_scope_references_preserve_trial_balance(self) -> None:
        """Canonical scope identities still return the posted debit and credit facts."""
        balances = self.ledger.trial_balance(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            through_date=date(2026, 8, 31),
        )

        self.assertEqual(balances["110100"].debit_total, 100)
        self.assertEqual(balances["410100"].credit_total, 100)


if __name__ == "__main__":
    unittest.main()
