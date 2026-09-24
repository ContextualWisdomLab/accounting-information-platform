"""Regression tests for journal line-number runtime admission."""

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


class _HostileLineNumber(int):
    """Expose caller-defined ordering before repository line-number admission."""

    def __lt__(self, other: object) -> bool:
        """Fail if constructor validation evaluates subclass ordering."""
        raise AssertionError("caller-defined line-number ordering executed")


class JournalLineNumberRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in positive integers for journal line identity."""

    def setUp(self) -> None:
        """Prepare one canonical posting policy and balanced proposal."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_line_number",
            legal_entity_reference="urn:cwl:legal_entity:line_number",
            accounting_book_reference="urn:cwl:accounting_book:line_number",
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
            posting_rule_version="line-number-v1",
        )
        self.proposal = JournalProposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf818",
            proposal_contract_version=1,
            idempotency_key="line-number-command-v1",
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            intended_book_role_code=self.policy.intended_book_role_code,
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 20),
            accounting_date=date(2026, 8, 20),
            source_payload_hash="sha256:" + "b" * 64,
            source_event_references=("urn:cwl:billing:invoice:line_number",),
            lines=(
                JournalLineProposal(1, "accounts_receivable", "100", "0"),
                JournalLineProposal(2, "usage_revenue", "0", "100"),
            ),
        )

    def test_constructor_rejects_bool_line_number(self) -> None:
        """Boolean truth values must not become retained journal line identity."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "line_number must be a positive integer",
        ):
            JournalLineProposal(True, "accounts_receivable", "100", "0")

    def test_constructor_rejects_int_subclass_before_ordering(self) -> None:
        """Caller-defined integer comparison must not participate in admission."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "line_number must be a positive integer",
        ):
            JournalLineProposal(
                _HostileLineNumber(1),
                "accounts_receivable",
                "100",
                "0",
            )

    def test_post_revalidates_mutated_line_number_before_replay(self) -> None:
        """A cached receipt must not hide invalid current line-number identity."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        object.__setattr__(self.proposal.lines[0], "line_number", True)

        with self.assertRaisesRegex(
            AccountingValidationError,
            "line_number must be a positive integer",
        ):
            ledger.post(self.proposal, self.policy)

        self.assertEqual(first.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)

    def test_exact_positive_builtin_integer_preserves_posting(self) -> None:
        """Canonical built-in integer line numbers retain existing semantics."""
        ledger = PostingLedger()
        first = ledger.post(self.proposal, self.policy)
        second = ledger.post(self.proposal, self.policy)

        self.assertEqual(first, second)
        self.assertEqual(first.line_count, 2)
        self.assertEqual(ledger.journal_count, 1)


if __name__ == "__main__":
    unittest.main()
