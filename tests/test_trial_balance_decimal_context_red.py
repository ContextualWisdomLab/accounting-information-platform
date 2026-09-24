"""RED contract for context-independent trial-balance arithmetic."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal, localcontext

from accounting_information_platform import (
    AccountBalance,
    AccountingPolicy,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class TrialBalanceDecimalContextContractTests(unittest.TestCase):
    """Require posted balance aggregation and net balance to ignore Decimal context."""

    def setUp(self) -> None:
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            accounting_book_reference="urn:cwl:accounting_book:primary_statutory",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 9, 1),
            open_period_end=date(2026, 9, 30),
            chart_account_mapping={
                "cash_clearing": "110900",
                "usage_revenue": "410100",
                "accounts_receivable": "110100",
            },
            accounting_policy_version="ifrs-v1",
            posting_rule_version="decimal-context-v1",
        )

    def _proposal(
        self,
        *,
        proposal_id: str,
        idempotency_key: str,
        source_hash_char: str,
        debit_amount: str,
        credit_role: str,
    ) -> JournalProposal:
        """Build one exactly balanced journal that debits the shared cash account."""
        return JournalProposal(
            proposal_id=proposal_id,
            proposal_contract_version=1,
            idempotency_key=idempotency_key,
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            transaction_date=date(2026, 9, 22),
            accounting_date=date(2026, 9, 22),
            source_payload_hash="sha256:" + source_hash_char * 64,
            source_event_references=(f"urn:cwl:source_event:{idempotency_key}",),
            lines=(
                JournalLineProposal(
                    line_number=1,
                    account_role_code="cash_clearing",
                    debit_amount=debit_amount,
                    credit_amount="0",
                ),
                JournalLineProposal(
                    line_number=2,
                    account_role_code=credit_role,
                    debit_amount="0",
                    credit_amount=debit_amount,
                ),
            ),
        )

    def test_trial_balance_keeps_low_order_units_under_small_precision(self) -> None:
        """Two posted journals cannot lose one debit unit while aggregating one account."""
        ledger = PostingLedger()
        ledger.post(
            self._proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf810",
                idempotency_key="trial-balance-large-v1",
                source_hash_char="b",
                debit_amount="10000000000000000000000000000",
                credit_role="usage_revenue",
            ),
            self.policy,
        )
        ledger.post(
            self._proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf811",
                idempotency_key="trial-balance-one-v1",
                source_hash_char="c",
                debit_amount="1",
                credit_role="accounts_receivable",
            ),
            self.policy,
        )
        expected = Decimal("10000000000000000000000000001")

        with localcontext() as context:
            context.prec = 2
            balances = ledger.trial_balance(
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                accounting_book_reference=self.policy.accounting_book_reference,
                through_date=date(2026, 9, 30),
            )

        self.assertEqual(balances["110900"].debit_total, expected)

    def test_trial_balance_keeps_credit_units_under_small_precision(self) -> None:
        """Two posted journals cannot lose one credit unit on a shared account."""
        ledger = PostingLedger()
        ledger.post(
            self._proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf812",
                idempotency_key="trial-balance-credit-large-v1",
                source_hash_char="d",
                debit_amount="10000000000000000000000000000",
                credit_role="usage_revenue",
            ),
            self.policy,
        )
        ledger.post(
            self._proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf813",
                idempotency_key="trial-balance-credit-one-v1",
                source_hash_char="e",
                debit_amount="1",
                credit_role="usage_revenue",
            ),
            self.policy,
        )
        expected = Decimal("10000000000000000000000000001")

        with localcontext() as context:
            context.prec = 2
            balances = ledger.trial_balance(
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                accounting_book_reference=self.policy.accounting_book_reference,
                through_date=date(2026, 9, 30),
            )

        self.assertEqual(balances["410100"].credit_total, expected)

    def test_net_balance_is_exact_under_small_precision(self) -> None:
        """AccountBalance.net_balance cannot round an exact low-order unit away."""
        expected = Decimal("10000000000000000000000000001")
        balance = AccountBalance(
            chart_account_code="110900",
            debit_total=expected,
            credit_total=Decimal("0"),
        )

        with localcontext() as context:
            context.prec = 2
            net_balance = balance.net_balance

        self.assertEqual(net_balance, expected)


if __name__ == "__main__":
    unittest.main()
