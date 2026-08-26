"""Current-head review regressions for command identity and operator-facing errors."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalLineProposal,
    JournalProposal,
    PostingLedger,
    accept_adjusting_journal,
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

    def test_out_of_policy_reversal_does_not_invent_closed_period_status(self) -> None:
        """A date-range failure must not claim a fiscal-period status it did not inspect."""
        with self.assertRaises(AccountingValidationError) as captured:
            self.ledger.reverse(
                self.receipt.journal_reference,
                date(2026, 9, 1),
                "billing_correction",
                self.policy,
                reversal_idempotency_key="review-reversal-outside-policy-v1",
            )

        message = str(captured.exception)
        self.assertIn("outside the permitted accounting policy date range", message)
        self.assertIn("Supply a reversal_date within the policy range", message)
        self.assertNotIn("closed fiscal period", message)


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


class AdjustingJournalExactDecimalBoundaryTests(unittest.TestCase):
    """Reject JSON numeric coercion before an adjusting journal can reach storage."""

    def test_json_numeric_amounts_fail_before_database_work(self) -> None:
        """Integers and binary floats are not canonical exact-decimal command values."""
        tenant_reference = "urn:cwl:tenant_adjusting_decimal"
        for raw_amount in (25000, 25000.5):
            with self.subTest(raw_amount=raw_amount):
                payload = {
                    "tenant_reference": tenant_reference,
                    "legal_entity_reference": "urn:cwl:legal_entity:ADJ",
                    "accounting_book_reference": "urn:cwl:accounting_book:ADJ",
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                    "journal_date": "2026-08-22",
                    "idempotency_key": f"adjusting-decimal-{type(raw_amount).__name__}",
                    "journal_description": "Exact-decimal boundary regression",
                    "journal_lines": [
                        {
                            "chart_account_code": "110100",
                            "debit_credit_code": "debit",
                            "currency_code": "KRW",
                            "amount": raw_amount,
                        },
                        {
                            "chart_account_code": "410100",
                            "debit_credit_code": "credit",
                            "currency_code": "KRW",
                            "amount": str(raw_amount),
                        },
                    ],
                }
                with mock.patch(
                    "accounting_information_platform.accept.PostgresPostingLedger"
                ) as ledger_type:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "amount.*exact decimal string|exact decimal.*amount",
                    ):
                        accept_adjusting_journal(
                            payload,
                            "postgresql://unused.example.invalid/accounting",
                            tenant_reference,
                        )
                    ledger_type.assert_not_called()

    def test_core_decimal_error_names_the_next_action(self) -> None:
        """Core validation tells an upstream caller how to correct a malformed amount."""
        with self.assertRaises(AccountingValidationError) as captured:
            JournalLineProposal(1, "accounts_receivable", "1.0000001", "0")

        message = str(captured.exception)
        self.assertIn("canonical non-negative decimal", message)
        self.assertIn("Supply a non-negative decimal string", message)
        self.assertIn("then retry ingest", message)


class CallerFacingStorageCopyTests(unittest.TestCase):
    """Keep storage implementation names and operator-owned repairs out of caller guidance."""

    def test_validation_error_sanitizes_known_storage_boundaries(self) -> None:
        """Shared caller errors hide implementation names and route operator-owned recovery."""
        cases = (
            (
                "the database session is not provisioned for this tenant. Ask the platform operator to verify tenant provisioning, then retry the request.",
                "this deployment is not provisioned for this tenant",
                "database session",
            ),
            (
                "Fiscal period 2026-08 has no control row for this accounting book. Repair the fiscal-period control data for this book, then retry the close.",
                "Ask the platform operator to restore the fiscal-period control data for this book, then retry the close.",
                "Repair the fiscal-period control data",
            ),
            (
                "balance_basis_code=post_close requires a stored trial_balance_snapshot. Hard-close the period, then retry the trial-balance read.",
                "requires stored close evidence",
                "trial_balance_snapshot",
            ),
            (
                "Fiscal period 2026-08 is hard_closed without a trial-balance snapshot. Restore the trial_balance_snapshot for this book from the journal population, then retry the trial-balance read.",
                "Ask the platform operator to restore the close evidence for this book from the authoritative journal history",
                "trial_balance_snapshot",
            ),
        )

        for raw_message, expected, forbidden in cases:
            with self.subTest(raw_message=raw_message):
                message = str(AccountingValidationError(raw_message))
                self.assertIn(expected, message)
                self.assertNotIn(forbidden, message)


if __name__ == "__main__":
    unittest.main()
