"""Regression tests for the closed Billing pull and origin contracts."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.billing_pull import (
    pull_journal_proposal,
    pull_validated_journal_proposals,
)


_TENANT = "urn:cwl:tenant_001"
_ALLOWED_ORIGIN = "https://billing.example.test"
_UNTRUSTED_ORIGIN = "https://untrusted.example.test"
_VALID_PROPOSAL = {
    "proposal_id": "019d7b92-6ff5-7a7f-b61c-962c0f4bf619",
    "proposal_status": "validated",
}


class BillingListClosedContractTests(unittest.TestCase):
    """Reject list-envelope drift instead of silently truncating accounting intake."""

    def _pull_document(self, document: dict[str, object]) -> object:
        env = {"BILLING_BASE_URL": _ALLOWED_ORIGIN, "BILLING_ALLOWED_ORIGINS": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch(
                "accounting_information_platform.billing_pull._billing_get",
                return_value=document,
            ):
                return pull_validated_journal_proposals(_ALLOWED_ORIGIN, _TENANT)

    def test_rejects_unknown_envelope_keys(self) -> None:
        """A future or misspelled key cannot be accepted as today's closed contract."""
        with self.assertRaisesRegex(AccountingValidationError, "list contract"):
            self._pull_document(
                {
                    "journal_proposals": [_VALID_PROPOSAL],
                    "next_cursor": None,
                    "has_more": True,
                }
            )

    def test_requires_explicit_null_or_nonempty_next_cursor(self) -> None:
        """Missing, empty, or non-string cursors cannot silently terminate pagination."""
        invalid_documents = (
            {"journal_proposals": [_VALID_PROPOSAL]},
            {"journal_proposals": [_VALID_PROPOSAL], "next_cursor": ""},
            {"journal_proposals": [_VALID_PROPOSAL], "next_cursor": 7},
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaisesRegex(AccountingValidationError, "next_cursor"):
                    self._pull_document(document)

    def test_rejects_invalid_or_nonvalidated_list_items(self) -> None:
        """Every returned list item must be a validated proposal object."""
        invalid_items = (
            "not-an-object",
            {"proposal_id": "x", "proposal_status": "draft"},
        )
        for item in invalid_items:
            with self.subTest(item=item):
                with self.assertRaisesRegex(AccountingValidationError, "validated proposal"):
                    self._pull_document(
                        {"journal_proposals": [item], "next_cursor": None}
                    )


class BillingPublicFetchOriginTests(unittest.TestCase):
    """Every public Billing fetch path enforces operator-configured origin authority."""

    def test_list_fetch_rejects_unconfigured_origin_before_network_call(self) -> None:
        """A direct library caller cannot bypass the Billing origin allowlist."""
        env = {"BILLING_BASE_URL": _ALLOWED_ORIGIN, "BILLING_ALLOWED_ORIGINS": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch(
                "accounting_information_platform.billing_pull._billing_get"
            ) as billing_get:
                with self.assertRaisesRegex(AccountingValidationError, "allowed Billing origin"):
                    pull_validated_journal_proposals(_UNTRUSTED_ORIGIN, _TENANT)
                billing_get.assert_not_called()

    def test_single_fetch_rejects_unconfigured_origin_before_network_call(self) -> None:
        """The one-proposal public fetch has the same origin policy as list fetches."""
        env = {"BILLING_BASE_URL": _ALLOWED_ORIGIN, "BILLING_ALLOWED_ORIGINS": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch(
                "accounting_information_platform.billing_pull._billing_get"
            ) as billing_get:
                with self.assertRaisesRegex(AccountingValidationError, "allowed Billing origin"):
                    pull_journal_proposal(_UNTRUSTED_ORIGIN, _TENANT, "proposal-1")
                billing_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
