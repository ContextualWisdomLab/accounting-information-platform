"""Regression tests for reversal idempotency-key runtime admission."""

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


class _HostileReversalKey(str):
    """Fail if caller-defined trimming reaches reversal command identity."""

    def strip(self, chars: str | None = None) -> str:
        """Expose missing repository admission before command normalization."""
        raise AssertionError("caller-defined reversal key strip executed")


class ReversalIdempotencyKeyRuntimeDomainRedTests(unittest.TestCase):
    """Require repository-owned string behavior for explicit reversal identity."""

    def setUp(self) -> None:
        """Post one canonical journal eligible for same-period reversal."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_reversal_idempotency",
            legal_entity_reference="urn:cwl:legal_entity:reversal_idempotency",
            accounting_book_reference="urn:cwl:accounting_book:reversal_idempotency",
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
            posting_rule_version="reversal-idempotency-v1",
        )
        self.ledger = PostingLedger()
        receipt = self.ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf817",
                proposal_contract_version=1,
                idempotency_key="reversal-idempotency-original-v1",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
                source_payload_hash="sha256:" + "a" * 64,
                source_event_references=("urn:cwl:billing:invoice:reversal_idempotency",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            self.policy,
        )
        self.journal_reference = receipt.journal_reference

    def _reverse(self, command_key: object):
        """Issue one reversal while varying only explicit command identity type."""
        return self.ledger.reverse(
            journal_reference=self.journal_reference,
            reversal_date=date(2026, 8, 21),
            reversal_reason_code="correction",
            policy=self.policy,
            reversal_idempotency_key=command_key,  # type: ignore[arg-type]
        )

    def test_reversal_rejects_non_string_explicit_command_key(self) -> None:
        """A non-string command key must fail through accounting validation."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "reversal idempotency key must be a string",
        ):
            self._reverse(42)

        self.assertEqual(self.ledger.journal_count, 1)

    def test_reversal_rejects_hostile_string_subclass_before_strip(self) -> None:
        """Caller-defined string behavior must not participate in command identity."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "reversal idempotency key must be a string",
        ):
            self._reverse(_HostileReversalKey("reversal-command-v1"))

        self.assertEqual(self.ledger.journal_count, 1)

    def test_replay_rejects_hostile_subclass_before_cached_receipt(self) -> None:
        """A retained receipt must not authorize runtime-invalid replay identity."""
        first = self._reverse("reversal-command-v1")

        with self.assertRaisesRegex(
            AccountingValidationError,
            "reversal idempotency key must be a string",
        ):
            self._reverse(_HostileReversalKey("reversal-command-v1"))

        self.assertEqual(first.reversal_of_journal_reference, self.journal_reference)
        self.assertEqual(self.ledger.journal_count, 2)

    def test_built_in_string_preserves_existing_trim_and_replay_semantics(self) -> None:
        """Built-in strings keep the existing trim-normalized replay contract."""
        first = self._reverse("  reversal-command-v1  ")
        second = self._reverse("reversal-command-v1")

        self.assertEqual(first, second)
        self.assertEqual(self.ledger.journal_count, 2)


if __name__ == "__main__":
    unittest.main()
