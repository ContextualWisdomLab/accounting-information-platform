"""RFC 3986 regressions for financial-report URI value objects."""

from __future__ import annotations

import unittest

from accounting_information_platform.core import AccountingValidationError
from accounting_information_platform.financial_reporting import primitives


class FinancialReportingUriRfc3986Tests(unittest.TestCase):
    """Reject URI spellings that urllib parses but report identifiers cannot admit."""

    def test_absolute_uri_rejects_malformed_percent_encoding_and_backslash(self) -> None:
        """Every percent escape is a hex triplet and a raw backslash is not URI syntax."""
        for raw_value in (
            "https://example.com/%ZZ/taxonomy.xsd",
            "urn:cwl:taxonomy:%2G",
            "https://example.com\\taxonomy.xsd",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "absolute URI",
                ):
                    primitives._absolute_uri(raw_value, "taxonomy_uri")

    def test_absolute_uri_rejects_non_numeric_http_port(self) -> None:
        """RFC 3986 permits only digits in the authority port component."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            "absolute URI",
        ):
            primitives._absolute_uri(
                "https://example.com:accounting/taxonomy.xsd",
                "taxonomy_uri",
            )

    def test_absolute_uri_rejects_http_authority_without_host(self) -> None:
        """HTTP(S) report identifiers require a non-empty origin host."""
        for raw_value in (
            "https://:443/taxonomy.xsd",
            "https://reporting-user@/taxonomy.xsd",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "absolute URI",
                ):
                    primitives._absolute_uri(raw_value, "taxonomy_uri")

    def test_absolute_uri_retains_valid_percent_encoding(self) -> None:
        """RFC 3986 percent-encoded octets remain valid absolute URI data."""
        self.assertEqual(
            primitives._absolute_uri(
                "https://example.com/taxonomy%20schema.xsd",
                "taxonomy_uri",
            ),
            "https://example.com/taxonomy%20schema.xsd",
        )


if __name__ == "__main__":
    unittest.main()
