"""Regression tests for trial-balance cutoff-date runtime admission."""

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
    """Detect caller-defined ordering before repository date admission."""

    def __lt__(self, other: object) -> bool:
        """Fail if trial-balance filtering delegates ordering to the caller."""
        raise RuntimeError("caller-defined date ordering executed")


class TrialBalanceDateRuntimeDomainRedTests(unittest.TestCase):
    """Require an exact calendar cutoff before trial-balance filtering."""

    def setUp(self) -> None:
        """Create one canonical posted journal for a deterministic balance read."""
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_trial_balance_date",
            legal_entity_reference="urn:cwl:legal_entity:trial_balance_date",
            accounting_book_reference="urn:cwl:accounting_book:trial_balance_date",
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
            posting_rule_version="trial-balance-date-v1",
        )
        self.ledger = PostingLedger()
        self.ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf813",
                proposal_contract_version=1,
                idempotency_key="trial-balance-date-original-v1",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
                source_payload_hash="sha256:" + "c" * 64,
                source_event_references=("urn:cwl:billing:invoice:trial_balance_date",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            self.policy,
        )

    def _read(self, through_date: date) -> dict[str, object]:
        """Read the fixed tenant/entity/book scope through one supplied cutoff."""
        return self.ledger.trial_balance(
            tenant_reference=self.policy.tenant_reference,
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            through_date=through_date,
        )

    def test_trial_balance_rejects_non_exact_cutoff_dates(self) -> None:
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
                    "through_date must be an exact calendar date",
                ):
                    self._read(invalid_date)  # type: ignore[arg-type]

    def test_exact_calendar_cutoff_preserves_trial_balance(self) -> None:
        """An exact built-in date preserves the canonical balance read."""
        balances = self._read(date(2026, 8, 31))

        self.assertEqual(balances["110100"].debit_total, 100)
        self.assertEqual(balances["410100"].credit_total, 100)


if __name__ == "__main__":
    unittest.main()
