"""Regression contracts for operator-configured Billing origin boundaries."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from accounting_information_platform.billing_pull import _configured_billing_bases
from accounting_information_platform.core import AccountingValidationError


class BillingConfiguredOriginTests(unittest.TestCase):
    """Keep Billing fetch configuration constrained to an origin, not a URL path."""

    def test_primary_billing_base_url_rejects_path(self) -> None:
        """A configured path must fail before AIS can append its fixed Billing API path."""
        with mock.patch.dict(
            os.environ,
            {
                "BILLING_BASE_URL": "https://billing.example.test/v1",
                "BILLING_ALLOWED_ORIGINS": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "origin"):
                _configured_billing_bases()


if __name__ == "__main__":
    unittest.main()
