"""Regression tests for unsafe financial-reporting input boundaries."""

from __future__ import annotations

import copy
import unittest
from decimal import Decimal

from accounting_information_platform import build_financial_report_artifact
from accounting_information_platform.financial_reporting import primitives
from accounting_information_platform.core import AccountingValidationError
from financial_reporting_fixtures import _report_context, _statement_package


class FinancialReportingInputHardeningTests(unittest.TestCase):
    """Reject unsafe numeric inputs and internally torn report provenance."""

    def test_binary_floating_amounts_are_rejected(self) -> None:
        """Do not silently convert a binary float into an accounting Decimal."""
        statement_package = copy.deepcopy(_statement_package())
        statement_package["income_statement"]["statement_lines"][0][
            "credit_amount"
        ] = 1200.5
        with self.assertRaisesRegex(
            AccountingValidationError,
            "binary floating-point",
        ):
            build_financial_report_artifact(
                statement_package,
                _report_context(),
            )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "binary floating-point",
        ):
            primitives._amount(0.1, "amount")

    def test_amounts_must_fit_accounting_numeric_domain(self) -> None:
        """Reject finite values that cannot fit PostgreSQL numeric(38, 6)."""
        for raw_value in (
            "1e32",
            "0.0000001",
            "1e1000000",
            "1e-1000000",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    r"numeric\(38, 6\)",
                ):
                    primitives._amount(raw_value, "amount")

        maximum_amount = "9" * 32 + "." + "9" * 6
        self.assertEqual(
            primitives._amount(maximum_amount, "amount"),
            Decimal(maximum_amount),
        )
        self.assertEqual(
            primitives._amount("0.000001", "amount"),
            Decimal("0.000001"),
        )

    def test_income_and_financial_position_lines_require_chart_accounts(self) -> None:
        """Keep ledger-backed statement lines traceable to a chart-account code."""
        for statement_type in ("income_statement", "balance_sheet"):
            statement_package = copy.deepcopy(_statement_package())
            statement_package[statement_type]["statement_lines"][0][
                "chart_account_code"
            ] = ""
            with self.subTest(statement_type=statement_type):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "chart_account_code",
                ):
                    build_financial_report_artifact(
                        statement_package,
                        _report_context(),
                    )

    def test_snapshot_references_must_be_consistent_inside_supplied_package(self) -> None:
        """Reject statement packages that claim different current or comparison snapshots."""
        for snapshot_key in (
            "snapshot_record_id",
            "comparison_snapshot_record_id",
        ):
            statement_package = copy.deepcopy(_statement_package())
            statement_package["cash_flow"][snapshot_key] = "different-snapshot"
            with self.subTest(snapshot_key=snapshot_key):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "snapshot references do not match",
                ):
                    build_financial_report_artifact(
                        statement_package,
                        _report_context(),
                    )

    def test_absolute_uri_rejects_unescaped_whitespace(self) -> None:
        """Do not accept whitespace that makes XBRL URI attributes non-canonical."""
        for raw_value in (
            "https://example.com/taxonomy schema.xsd",
            "urn:cwl:taxonomy: profile",
            "https://example.com/taxonomy\tschema.xsd",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "absolute URI",
                ):
                    primitives._absolute_uri(raw_value, "taxonomy_uri")

    def test_invalid_unicode_never_escapes_as_an_encoding_error(self) -> None:
        """Convert invalid Unicode input into the domain validation error contract."""
        with self.assertRaises(AccountingValidationError):
            primitives._json_bytes("\ud800", "invalid unicode")


if __name__ == "__main__":
    unittest.main()
