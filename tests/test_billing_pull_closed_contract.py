"""Regression tests for the closed Billing pull and origin contracts."""

from __future__ import annotations

import os
import unittest
from unittest import mock
from urllib.parse import urlparse

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.billing_pull import (
    _billing_get,
    _open_billing_connection,
    _require_billing_base_url,
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

    def test_rejects_a_non_array_journal_proposal_collection(self) -> None:
        """The closed list envelope cannot carry an object in place of its array."""
        with self.assertRaisesRegex(AccountingValidationError, "must be an array"):
            self._pull_document({"journal_proposals": {}, "next_cursor": None})


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

    def test_empty_billing_origin_is_rejected(self) -> None:
        """Whitespace cannot become a remote Billing destination."""
        with self.assertRaisesRegex(AccountingValidationError, "BILLING_BASE_URL is empty"):
            _require_billing_base_url("  ")

    def test_invalid_fetch_url_fails_before_network_call(self) -> None:
        """A non-HTTP URL fails before a socket can be opened."""
        with self.assertRaisesRegex(AccountingValidationError, "http or https origin"):
            _billing_get("file:///tmp/billing", _TENANT, {})

    def test_network_oserror_becomes_an_actionable_pull_error(self) -> None:
        """Socket failures are converted to the operator retry contract."""
        with mock.patch(
            "accounting_information_platform.billing_pull._open_billing_connection",
            side_effect=OSError("offline"),
        ):
            with self.assertRaisesRegex(AccountingValidationError, "Retry the Billing pull"):
                _billing_get(
                    "https://billing.example.test/v1/journal-proposals",
                    _TENANT,
                    {},
                )

    def test_https_connection_uses_default_ssl_context(self) -> None:
        """HTTPS wraps the connected socket with certificate verification and SNI."""
        connection = mock.Mock()
        raw_socket = object()
        connection.sock = raw_socket
        tls_socket = object()
        with mock.patch(
            "accounting_information_platform.billing_pull.http.client.HTTPConnection",
            return_value=connection,
        ) as constructor, mock.patch(
            "accounting_information_platform.billing_pull.ssl.create_default_context"
        ) as create_context:
            create_context.return_value.wrap_socket.return_value = tls_socket
            result = _open_billing_connection(urlparse(_ALLOWED_ORIGIN))
        constructor.assert_called_once_with("billing.example.test", 443, timeout=5)
        connection.connect.assert_called_once_with()
        create_context.return_value.wrap_socket.assert_called_once_with(
            raw_socket, server_hostname="billing.example.test"
        )
        self.assertIs(connection.sock, tls_socket)
        self.assertIs(result, connection)


if __name__ == "__main__":
    unittest.main()
