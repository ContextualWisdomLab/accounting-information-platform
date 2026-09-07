"""URI-standard regressions for financial-report URI value objects."""

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

    def test_absolute_uri_rejects_http_userinfo(self) -> None:
        """Caller-supplied HTTP(S) identifiers cannot retain authority userinfo."""
        for raw_value in (
            "https://reporting-user@example.com/taxonomy.xsd",
            "https://reporting-user:secret@example.com/taxonomy.xsd",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "absolute URI",
                ):
                    primitives._absolute_uri(raw_value, "taxonomy_uri")

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

    def test_absolute_uri_rejects_urn_authority_and_missing_nss(self) -> None:
        """RFC 8141 URNs require an NID and NSS rather than URI authority syntax."""
        for raw_value in (
            "urn://example.com/taxonomy",
            "urn:cwl",
            "urn::taxonomy",
            "urn:cwl:",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "URN namespace",
                ):
                    primitives._absolute_uri(raw_value, "taxonomy_uri")

    def test_absolute_uri_retains_valid_percent_encoding_and_urn(self) -> None:
        """Valid percent-encoded URI data and an RFC 8141 assigned-name remain valid."""
        self.assertEqual(
            primitives._absolute_uri(
                "https://example.com/taxonomy%20schema.xsd",
                "taxonomy_uri",
            ),
            "https://example.com/taxonomy%20schema.xsd",
        )
        self.assertEqual(
            primitives._absolute_uri(
                "urn:cwl:taxonomy:ifrs-2025",
                "taxonomy_uri",
            ),
            "urn:cwl:taxonomy:ifrs-2025",
        )


if __name__ == "__main__":
    unittest.main()
