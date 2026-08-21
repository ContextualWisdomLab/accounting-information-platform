"""Current-head review regressions for command identity and operator-facing errors."""

from __future__ import annotations

import unittest
from datetime import date

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
    accept_home_tax_submission,
)


class ReversalCommandIdentityTests(unittest.TestCase):
    """Distinguish key reuse from an already-reversed journal identity."""

    def setUp(self) -> None:
        self.policy = AccountingPolicy(
            tenant_reference="urn:cwl:tenant_review_regression",
            legal_entity_reference="urn:cwl:legal_entity:review_regression",
            accounting_book_reference="urn:cwl:accounting_book:review_regression",
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
            posting_rule_version="review-regression-v1",
        )
        self.ledger = PostingLedger()
        self.receipt = self.ledger.post(
            JournalProposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf700",
                proposal_contract_version=1,
                idempotency_key="review-regression-post-v1",
                tenant_reference=self.policy.tenant_reference,
                legal_entity_reference=self.policy.legal_entity_reference,
                intended_book_role_code=self.policy.intended_book_role_code,
                transaction_currency="KRW",
                transaction_date=date(2026, 8, 31),
                accounting_date=date(2026, 8, 31),
                source_payload_hash="sha256:" + "a" * 64,
                source_event_references=("urn:cwl:event:review_regression",),
                lines=(
                    JournalLineProposal(1, "accounts_receivable", "100", "0"),
                    JournalLineProposal(2, "usage_revenue", "0", "100"),
                ),
            ),
            self.policy,
        )

    def test_distinct_reversal_key_reports_already_reversed_not_key_reuse(self) -> None:
        """A new key cannot replay an existing reversal and must not blame key reuse."""
        self.ledger.reverse(
            self.receipt.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key="review-reversal-command-v1",
        )

        with self.assertRaisesRegex(AccountingValidationError, "already reversed") as captured:
            self.ledger.reverse(
                self.receipt.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
                reversal_idempotency_key="review-reversal-command-v2",
            )

        self.assertNotIsInstance(captured.exception, IdempotencyConflictError)
        self.assertEqual(self.ledger.journal_count, 2)

    def test_same_reversal_key_with_changed_payload_is_idempotency_conflict(self) -> None:
        """The same command key with changed immutable command evidence remains a conflict."""
        self.ledger.reverse(
            self.receipt.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key="review-reversal-command-v1",
        )

        with self.assertRaisesRegex(IdempotencyConflictError, "different payload"):
            self.ledger.reverse(
                self.receipt.journal_reference,
                date(2026, 8, 31),
                "operator_correction",
                self.policy,
                reversal_idempotency_key="review-reversal-command-v1",
            )


class HomeTaxCommandProvenanceBoundaryTests(unittest.TestCase):
    """Require immutable source provenance before any HomeTax command work."""

    def test_home_tax_requires_source_hash_before_scope_or_database_work(self) -> None:
        """A write command without canonical source evidence fails before DB access."""
        tenant_reference = "urn:cwl:tenant_home_tax_provenance"
        with self.assertRaisesRegex(AccountingValidationError, "source_payload_hash"):
            accept_home_tax_submission(
                {
                    "tenant_reference": tenant_reference,
                    "idempotency_key": "home-tax-provenance-v1",
                    "source_payload_reference": "urn:cwl:evidence:home_tax_provenance_v1",
                },
                "postgresql://unused.example.invalid/accounting",
                tenant_reference,
            )

    def test_home_tax_requires_source_reference_before_scope_or_database_work(self) -> None:
        """A write command without an immutable source locator fails before DB access."""
        tenant_reference = "urn:cwl:tenant_home_tax_provenance"
        with self.assertRaisesRegex(AccountingValidationError, "source_payload_reference"):
            accept_home_tax_submission(
                {
                    "tenant_reference": tenant_reference,
                    "idempotency_key": "home-tax-provenance-v1",
                    "source_payload_hash": "sha256:" + "b" * 64,
                },
                "postgresql://unused.example.invalid/accounting",
                tenant_reference,
            )


if __name__ == "__main__":
    unittest.main()
