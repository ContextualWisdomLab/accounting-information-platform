"""Regression tests for fail-closed Billing origin normalization."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.billing_pull import _canonical_billing_origin


class BillingOriginContractTests(unittest.TestCase):
    """Keep malformed configured Billing origins inside the accounting validation contract."""

    def test_invalid_port_is_accounting_validation_error(self) -> None:
        """A malformed port must not escape as a raw urllib ValueError."""
        with self.assertRaisesRegex(AccountingValidationError, "http or https origin"):
            _canonical_billing_origin("https://billing.example.test:not-a-port")

    def test_malformed_ipv6_is_accounting_validation_error(self) -> None:
        """Malformed IPv6 syntax must fail closed before any Billing request is attempted."""
        with self.assertRaisesRegex(AccountingValidationError, "http or https origin"):
            _canonical_billing_origin("https://[2001:db8::1")

    def test_loopback_cannot_be_reenabled_by_allowlist(self) -> None:
        """An explicit allowlist entry does not override the loopback prohibition."""
        from accounting_information_platform.billing_pull import _configured_billing_bases

        with mock.patch.dict(
            os.environ,
            {
                "BILLING_BASE_URL": "https://billing.example.test",
                "BILLING_ALLOWED_ORIGINS": "http://127.0.0.1:8080",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "loopback or link-local"):
                _configured_billing_bases()


if __name__ == "__main__":
    unittest.main()
