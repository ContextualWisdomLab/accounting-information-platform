"""RED contract for context-independent General Ledger balance arithmetic."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal, localcontext

from accounting_information_platform import (
    AccountingValidationError,
    JournalLineProposal,
    JournalProposal,
)


class JournalBalanceDecimalContextContractTests(unittest.TestCase):
    """Require double-entry balance checks and totals to ignore Decimal context."""

    @staticmethod
    def _proposal(*, credit_total: str) -> JournalProposal:
        """Build one proposal whose debit side is a large amount plus one unit."""
        return JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf800",
            proposal_contract_version=1,
            idempotency_key="decimal-context-balance-v1",
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            transaction_date=date(2026, 9, 22),
            accounting_date=date(2026, 9, 22),
            source_payload_hash="sha256:" + "a" * 64,
            source_event_references=("urn:cwl:source_event:decimal_context_balance",),
            lines=(
                JournalLineProposal(
                    line_number=1,
                    account_role_code="cash_clearing",
                    debit_amount="10000000000000000000000000000",
                    credit_amount="0",
                ),
                JournalLineProposal(
                    line_number=2,
                    account_role_code="accounts_receivable",
                    debit_amount="1",
                    credit_amount="0",
                ),
                JournalLineProposal(
                    line_number=3,
                    account_role_code="usage_revenue",
                    debit_amount="0",
                    credit_amount=credit_total,
                ),
            ),
        )

    def test_unbalanced_proposal_cannot_round_into_balance(self) -> None:
        """A one-unit imbalance remains visible at the default 28-digit precision."""
        with localcontext() as context:
            context.prec = 28
            with self.assertRaisesRegex(AccountingValidationError, "must balance"):
                self._proposal(credit_total="10000000000000000000000000000")

    def test_balanced_proposal_and_totals_remain_exact_at_low_precision(self) -> None:
        """A truly balanced large journal remains balanced and reports exact totals."""
        expected = Decimal("10000000000000000000000000001")

        with localcontext() as context:
            context.prec = 2
            proposal = self._proposal(credit_total=str(expected))
            debit_total = proposal.debit_total
            credit_total = proposal.credit_total

        self.assertEqual(debit_total, expected)
        self.assertEqual(credit_total, expected)


if __name__ == "__main__":
    unittest.main()
