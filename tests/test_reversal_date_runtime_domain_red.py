"""Regression tests for reversal-date runtime-domain admission."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
)


class _ExplodingDate(date):
    """Detect caller-defined date behavior before repository admission."""

    def isoformat(self) -> str:
        """Fail if reversal hashing reaches subclass behavior before validation."""
        raise RuntimeError("caller-defined date behavior executed")


class ReversalDateRuntimeDomainRedTests(unittest.TestCase):
    """Require exact calendar dates before reversal hashing or replay lookup."""

    def setUp(self) -> None:
        """Create one posted journal under a canonical open-period policy."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_reversal_date",
            legal_entity_reference="urn:cwl:legal_entity:reversal_date",
            accounting_book_reference="urn:cwl:accounting_book:reversal_date",
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
            posting_rule_version="reversal-date-v1",
        )
        self.ledger = PostingLedger()
        self.original = self.ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf712",
                proposal_contract_version=1,
                idempotency_key="reversal-date-original-v1",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
                source_payload_hash="sha256:" + "b" * 64,
                source_event_references=("urn:cwl:billing:invoice:reversal_date",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            self.policy,
        )

    def test_reversal_rejects_non_exact_dates_before_hashing(self) -> None:
        """Strings, datetimes, and date subclasses must fail through AIS validation."""
        invalid_dates = (
            "2026-08-31",
            datetime(2026, 8, 31, 0, 0),
            _ExplodingDate(2026, 8, 31),
        )
        for invalid_date in invalid_dates:
            with self.subTest(invalid_type=type(invalid_date).__name__):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "reversal_date must be an exact calendar date",
                ):
                    self.ledger.reverse(
                        self.original.journal_reference,
                        invalid_date,  # type: ignore[arg-type]
                        "billing_correction",
                        self.policy,
                    )
        self.assertEqual(self.ledger.journal_count, 1)

    def test_reversal_date_is_revalidated_before_cached_replay(self) -> None:
        """A cached receipt must not authorize a datetime-shaped replay command."""
        self.ledger.reverse(
            self.original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "reversal_date must be an exact calendar date",
        ):
            self.ledger.reverse(
                self.original.journal_reference,
                datetime(2026, 8, 31, 0, 0),
                "billing_correction",
                self.policy,
            )
        self.assertEqual(self.ledger.journal_count, 2)

    def test_exact_calendar_date_remains_valid(self) -> None:
        """An exact built-in date preserves the supported reversal contract."""
        receipt = self.ledger.reverse(
            self.original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )

        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(receipt.reversal_of_journal_reference, self.original.journal_reference)
        self.assertEqual(self.ledger.journal_count, 2)


if __name__ == "__main__":
    unittest.main()
