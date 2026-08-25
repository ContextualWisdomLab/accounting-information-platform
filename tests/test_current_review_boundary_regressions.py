"""Regressions for validated current-head review findings."""

from __future__ import annotations

import unittest
from unittest import mock
from urllib.parse import urlparse

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.accept import _period_status_from_close_payload
from accounting_information_platform.billing_pull import _open_billing_connection


class PeriodCloseStatusBoundaryTests(unittest.TestCase):
    """Keep irreversible close commands on the published status vocabulary."""

    def test_unknown_period_status_fails_closed_at_command_boundary(self) -> None:
        """A non-empty unknown close status must not reach durable close processing."""
        with self.assertRaisesRegex(
            AccountingValidationError, "soft_closed or hard_closed"
        ):
            _period_status_from_close_payload({"period_status_code": "pending_close"})


class BillingConnectionCleanupTests(unittest.TestCase):
    """Close partially opened Billing sockets when HTTPS setup fails."""

    @mock.patch("accounting_information_platform.billing_pull.http.client.HTTPConnection")
    def test_https_connect_failure_closes_partial_connection(
        self, connection_type: mock.Mock
    ) -> None:
        """A failed TCP connect still closes the HTTPConnection object."""
        connection = mock.Mock()
        connection.connect.side_effect = OSError("connect failed")
        connection_type.return_value = connection

        with self.assertRaises(OSError):
            _open_billing_connection(urlparse("https://billing.example.test"))

        connection.close.assert_called_once_with()

    @mock.patch("accounting_information_platform.billing_pull.ssl.create_default_context")
    @mock.patch("accounting_information_platform.billing_pull.http.client.HTTPConnection")
    def test_https_tls_wrap_failure_closes_connected_socket(
        self,
        connection_type: mock.Mock,
        context_factory: mock.Mock,
    ) -> None:
        """A failed TLS wrap closes the already-connected HTTPConnection."""
        connection = mock.Mock()
        connection.sock = object()
        connection_type.return_value = connection
        context = mock.Mock()
        context.wrap_socket.side_effect = OSError("TLS wrap failed")
        context_factory.return_value = context

        with self.assertRaises(OSError):
            _open_billing_connection(urlparse("https://billing.example.test"))

        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
