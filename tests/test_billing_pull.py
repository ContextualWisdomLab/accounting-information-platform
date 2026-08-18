"""Billing pull rejection tokens stay stable and do not invent a Billing status."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.billing_pull import (
    _rejected_proposal,
    _rejection_reason_code,
)


class BillingPullRejectionReasonTests(unittest.TestCase):
    """Map operator validation sentences to stable snake_case reason codes."""

    def test_maps_known_messages_and_defaults_unknown_text(self) -> None:
        """Each advertised token is derived from the existing error sentence."""
        cases = (
            (
                "Account role contract_liability is not mapped on this book.",
                "unknown_account_role",
            ),
            (
                "No account_role_mapping is effective for this book and accounting date.",
                "unknown_account_role",
            ),
            ("journal proposal must balance", "unbalanced_journal"),
            (
                "accounting date belongs to a closed fiscal period",
                "closed_period",
            ),
            (
                "Fiscal period 2026-08 is soft_closed. Open that period or post into an open period",
                "closed_period",
            ),
            ("Fiscal period 2026-08 is hard_closed.", "closed_period"),
            (
                "pull tenant_reference does not match the bound tenant.",
                "cross_tenant",
            ),
            (
                "debit_amount must be a canonical decimal string.",
                "invalid_amount",
            ),
            (
                "amount must be a canonical non-negative decimal",
                "invalid_amount",
            ),
            ("line number must be positive", "proposal_validation_failed"),
        )
        for message, reason_code in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    _rejection_reason_code(AccountingValidationError(message)),
                    reason_code,
                )

    def test_rejected_proposal_uses_billing_identity_and_next_action(self) -> None:
        """Rejected rows keep Billing identity and the operator next-action sentence."""
        error = AccountingValidationError(
            "Account role contract_liability is not mapped on this book. "
            "Create the account_role_mapping row, then retry posting."
        )
        document = _rejected_proposal(
            {
                "proposal_id": "019d7b92-6ff5-7a7f-b61c-962c0f4bf619",
                "idempotency_key": "urn:cwl:tenant_001:invoice_draft:x",
            },
            error,
        )
        missing_identity = _rejected_proposal({}, error)

        self.assertEqual(
            document,
            {
                "proposal_id": "019d7b92-6ff5-7a7f-b61c-962c0f4bf619",
                "idempotency_key": "urn:cwl:tenant_001:invoice_draft:x",
                "rejection_reason_code": "unknown_account_role",
                "rejection_message": str(error),
            },
        )
        self.assertEqual(missing_identity["proposal_id"], "")
        self.assertEqual(missing_identity["idempotency_key"], "")


if __name__ == "__main__":
    unittest.main()
