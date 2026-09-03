"""Validation tests for canonical financial-report artifacts."""

from __future__ import annotations

import copy
import unittest
import xml.etree.ElementTree as element_tree
from decimal import Decimal

from accounting_information_platform import financial_reporting as reporting
from accounting_information_platform.financial_reporting import artifact as artifact_module
from accounting_information_platform.financial_reporting import primitives as primitive_module
from accounting_information_platform.financial_reporting import statements as statement_module
from accounting_information_platform.financial_reporting import xbrl as xbrl_module
from accounting_information_platform.core import AccountingValidationError
from financial_reporting_fixtures import (
    _context_without_comparison,
    _package_without_comparison,
    _report_context,
    _statement_package,
    _valid_profile,
)


class ArtifactValidationTests(unittest.TestCase):
    """Exercise artifact inputs, statement identities, and cross-statement controls."""

    def test_rejects_public_input_types_and_json_incompatibility(self) -> None:
        """Reject non-mapping inputs, wrong contexts, and non-JSON source values."""
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact([], _report_context())
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(
                _statement_package(),
                object(),
            )
        invalid_package = copy.deepcopy(_statement_package())
        invalid_package["not_json"] = {1, 2}
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(
                invalid_package,
                _report_context(),
            )

    def test_noncomparative_artifact_uses_current_only(self) -> None:
        """Omit comparison facts, contexts, explanations, and optional scope keys."""
        statement_package = _package_without_comparison()
        report_artifact = reporting.build_financial_report_artifact(
            statement_package,
            _context_without_comparison(),
        )
        self.assertNotIn("comparison_fiscal_period_reference", report_artifact)
        self.assertNotIn(
            "net_income_change_amount",
            report_artifact["profit_and_loss_summary"],
        )
        self.assertEqual(
            [
                explanation_record["explanation_code"]
                for explanation_record in report_artifact["explanation_records"]
            ],
            [
                "profit_loss.current_summary",
                "financial_position.equation",
                "changes_in_equity.rollforward",
                "cash_flow.rollforward",
            ],
        )
        xbrl_export = reporting.export_xbrl_instance(
            report_artifact,
            _valid_profile(),
        )
        xml_root = element_tree.fromstring(xbrl_export["xbrl_instance"])
        namespaces = {"xbrli": xbrl_module._XBRLI_NAMESPACE}
        self.assertEqual(
            {
                context_element.attrib["id"]
                for context_element in xml_root.findall(
                    "xbrli:context",
                    namespaces,
                )
            },
            {"current_duration", "current_instant"},
        )

        package_without_scope = _package_without_comparison()
        package_without_scope.pop("statement_scope_code")
        for statement_type in statement_module._STATEMENT_TYPES:
            package_without_scope[statement_type].pop("statement_scope_code")
        artifact_without_scope = reporting.build_financial_report_artifact(
            package_without_scope,
            _context_without_comparison(),
        )
        self.assertNotIn("statement_scope_code", artifact_without_scope)

    def test_comparison_identity_and_context_must_be_complete(self) -> None:
        """Reject partial statement comparisons or unpaired comparison context."""
        partial_comparison = copy.deepcopy(_statement_package())
        partial_comparison["cash_flow"].pop("comparison_statement_lines")
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(
                partial_comparison,
                _report_context(),
            )

        missing_reference = copy.deepcopy(_statement_package())
        missing_reference.pop("comparison_fiscal_period_reference")
        for statement_type in statement_module._STATEMENT_TYPES:
            missing_reference[statement_type].pop(
                "comparison_fiscal_period_reference"
            )
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(
                missing_reference,
                _report_context(),
            )

        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(
                _package_without_comparison(),
                _report_context(),
            )
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(
                _statement_package(),
                _context_without_comparison(),
            )

    def test_statement_identity_and_shape_failures(self) -> None:
        """Reject missing statements and statement documents torn from the package."""
        invalid_packages: list[dict[str, object]] = []

        missing_statement = copy.deepcopy(_statement_package())
        missing_statement.pop("cash_flow")
        invalid_packages.append(missing_statement)

        wrong_type = copy.deepcopy(_statement_package())
        wrong_type["cash_flow"]["statement_type_code"] = "income_statement"
        invalid_packages.append(wrong_type)

        wrong_tenant = copy.deepcopy(_statement_package())
        wrong_tenant["cash_flow"]["tenant_reference"] = "other"
        invalid_packages.append(wrong_tenant)

        wrong_scope = copy.deepcopy(_statement_package())
        wrong_scope["cash_flow"]["statement_scope_code"] = "period"
        invalid_packages.append(wrong_scope)

        wrong_comparison = copy.deepcopy(_statement_package())
        wrong_comparison["cash_flow"][
            "comparison_fiscal_period_reference"
        ] = "other"
        invalid_packages.append(wrong_comparison)

        wrong_book = copy.deepcopy(_statement_package())
        wrong_book["cash_flow"]["book_reference"] = "other"
        invalid_packages.append(wrong_book)

        for invalid_package in invalid_packages:
            with self.subTest(invalid_package=invalid_package):
                with self.assertRaises(AccountingValidationError):
                    reporting.build_financial_report_artifact(
                        invalid_package,
                        _report_context(),
                    )

    def test_book_identity_supports_compatible_fallback_but_rejects_conflict(self) -> None:
        """Use one book alias when present and reject two different aliases."""
        fallback_package = copy.deepcopy(_statement_package())
        fallback_package.pop("book_reference")
        for statement_type in statement_module._STATEMENT_TYPES:
            fallback_package[statement_type].pop("book_reference")
        report_artifact = reporting.build_financial_report_artifact(
            fallback_package,
            _report_context(),
        )
        self.assertEqual(report_artifact["book_reference"], "primary-book")

        conflicting_package = copy.deepcopy(_statement_package())
        conflicting_package["accounting_book_reference"] = "other"
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(
                conflicting_package,
                _report_context(),
            )

    def test_statement_line_shape_failures(self) -> None:
        """Reject malformed rows, signs, two-sided lines, and inconsistent totals."""
        invalid_packages: list[dict[str, object]] = []

        def replace_statement_value(
            statement_type: str,
            field_name: str,
            field_value: object,
        ) -> dict[str, object]:
            """Return a package with one statement field replaced."""
            statement_package = copy.deepcopy(_statement_package())
            statement_package[statement_type][field_name] = field_value
            return statement_package

        invalid_packages.extend(
            (
                replace_statement_value(
                    "income_statement",
                    "statement_lines",
                    "bad",
                ),
                replace_statement_value(
                    "income_statement",
                    "statement_lines",
                    [1],
                ),
            )
        )
        for field_name, field_value in (
            ("account_role_code", "Bad Code"),
            ("account_class_code", "unknown"),
            ("chart_account_code", 1),
            ("chart_account_code", " bad"),
            ("debit_amount", "-1"),
        ):
            invalid_package = copy.deepcopy(_statement_package())
            invalid_package["income_statement"]["statement_lines"][0][
                field_name
            ] = field_value
            invalid_packages.append(invalid_package)

        two_sided_line = copy.deepcopy(_statement_package())
        two_sided_line["income_statement"]["statement_lines"][0][
            "debit_amount"
        ] = "1"
        invalid_packages.append(two_sided_line)

        wrong_total = copy.deepcopy(_statement_package())
        wrong_total["income_statement"]["total_credit_amount"] = "0"
        invalid_packages.append(wrong_total)

        wrong_net_income = copy.deepcopy(_statement_package())
        wrong_net_income["income_statement"]["net_income_amount"] = "999"
        invalid_packages.append(wrong_net_income)

        for invalid_package in invalid_packages:
            with self.subTest(invalid_package=invalid_package):
                with self.assertRaises(AccountingValidationError):
                    reporting.build_financial_report_artifact(
                        invalid_package,
                        _report_context(),
                    )

    def test_financial_position_and_rollforward_failures(self) -> None:
        """Reject each broken financial-position, equity, or cash-flow equation."""
        invalid_packages: list[dict[str, object]] = []

        broken_position = copy.deepcopy(_statement_package())
        broken_position["balance_sheet"]["statement_lines"][0][
            "debit_amount"
        ] = "1600.00"
        broken_position["balance_sheet"]["total_debit_amount"] = "1600.00"
        invalid_packages.append(broken_position)

        broken_equity_rollforward = copy.deepcopy(_statement_package())
        broken_equity_rollforward["changes_in_equity"]["statement_lines"][2][
            "credit_amount"
        ] = "90.00"
        broken_equity_rollforward["changes_in_equity"][
            "total_credit_amount"
        ] = "2390.25"
        invalid_packages.append(broken_equity_rollforward)

        broken_equity_income = copy.deepcopy(_statement_package())
        broken_equity_income["changes_in_equity"]["statement_lines"][1][
            "credit_amount"
        ] = "999.00"
        broken_equity_income["changes_in_equity"]["statement_lines"][3][
            "credit_amount"
        ] = "1198.75"
        broken_equity_income["changes_in_equity"][
            "total_credit_amount"
        ] = "2397.50"
        invalid_packages.append(broken_equity_income)

        broken_equity_position = copy.deepcopy(_statement_package())
        broken_equity_position["changes_in_equity"]["statement_lines"][2][
            "credit_amount"
        ] = "100.75"
        broken_equity_position["changes_in_equity"]["statement_lines"][3][
            "credit_amount"
        ] = "1201.00"
        broken_equity_position["changes_in_equity"][
            "total_credit_amount"
        ] = "2402.00"
        invalid_packages.append(broken_equity_position)

        broken_operations = copy.deepcopy(_statement_package())
        broken_operations["cash_flow"]["statement_lines"][2][
            "credit_amount"
        ] = "999.00"
        broken_operations["cash_flow"]["total_credit_amount"] = "4999.25"
        invalid_packages.append(broken_operations)

        broken_net_change = copy.deepcopy(_statement_package())
        broken_net_change["cash_flow"]["statement_lines"][5][
            "credit_amount"
        ] = "999.00"
        broken_net_change["cash_flow"]["total_credit_amount"] = "4999.25"
        invalid_packages.append(broken_net_change)

        broken_cash_rollforward = copy.deepcopy(_statement_package())
        broken_cash_rollforward["cash_flow"]["statement_lines"][7][
            "credit_amount"
        ] = "1499.00"
        broken_cash_rollforward["cash_flow"][
            "total_credit_amount"
        ] = "4999.25"
        invalid_packages.append(broken_cash_rollforward)

        broken_cash_income = copy.deepcopy(_statement_package())
        broken_cash_income["cash_flow"]["statement_lines"][0][
            "credit_amount"
        ] = "999.00"
        broken_cash_income["cash_flow"]["statement_lines"][1][
            "debit_amount"
        ] = "0"
        broken_cash_income["cash_flow"]["statement_lines"][1][
            "credit_amount"
        ] = "1.00"
        broken_cash_income["cash_flow"]["total_debit_amount"] = "0"
        broken_cash_income["cash_flow"]["total_credit_amount"] = "5000.00"
        invalid_packages.append(broken_cash_income)

        for invalid_package in invalid_packages:
            with self.subTest(invalid_package=invalid_package):
                with self.assertRaises(AccountingValidationError):
                    reporting.build_financial_report_artifact(
                        invalid_package,
                        _report_context(),
                    )

        split_cash_account = copy.deepcopy(_statement_package())
        split_cash_account["balance_sheet"]["statement_lines"][0][
            "debit_amount"
        ] = "1400.00"
        split_cash_account["balance_sheet"]["statement_lines"].append(
            {
                "chart_account_code": "110300",
                "account_role_code": "other_asset",
                "account_class_code": "asset",
                "debit_amount": "100.00",
                "credit_amount": "0",
            }
        )
        split_artifact = reporting.build_financial_report_artifact(
            split_cash_account,
            _report_context(),
        )
        self.assertEqual(
            split_artifact["profit_and_loss_summary"]["net_income_amount"],
            "1000.25",
        )

        no_cash_role = copy.deepcopy(_statement_package())
        no_cash_role["balance_sheet"]["statement_lines"][0][
            "account_role_code"
        ] = "other_cash"
        no_cash_artifact = reporting.build_financial_report_artifact(
            no_cash_role,
            _report_context(),
        )
        self.assertEqual(
            no_cash_artifact["profit_and_loss_summary"]["net_income_amount"],
            "1000.25",
        )

    def test_rollforward_role_failures_and_evidence_fallback_paths(self) -> None:
        """Reject unexpected, duplicate, and missing rollforward roles."""
        unexpected_role = copy.deepcopy(_statement_package())
        unexpected_role["changes_in_equity"]["statement_lines"][0][
            "account_role_code"
        ] = "unexpected"

        duplicate_role = copy.deepcopy(_statement_package())
        duplicate_role["changes_in_equity"]["statement_lines"][1][
            "account_role_code"
        ] = "opening_equity"

        missing_role = copy.deepcopy(_statement_package())
        missing_role["changes_in_equity"]["statement_lines"] = missing_role[
            "changes_in_equity"
        ]["statement_lines"][:-1]
        missing_role["changes_in_equity"]["total_credit_amount"] = "1200.00"

        for invalid_package in (
            unexpected_role,
            duplicate_role,
            missing_role,
        ):
            with self.subTest(invalid_package=invalid_package):
                with self.assertRaises(AccountingValidationError):
                    reporting.build_financial_report_artifact(
                        invalid_package,
                        _report_context(),
                    )

        self.assertEqual(statement_module._paths([], "revenue"), ["revenue"])
        fallback_line = statement_module._StatementLine(
            "other_asset",
            "asset",
            Decimal("1"),
            Decimal("0"),
            "balance_sheet.statement_lines[0]",
        )
        self.assertEqual(
            statement_module._paths([fallback_line], "revenue"),
            ["balance_sheet.statement_lines"],
        )

    def test_zero_and_direction_variants_are_canonical(self) -> None:
        """Normalize signed zero and classify negative and unchanged movements."""
        self.assertEqual(artifact_module._direction(Decimal("-1")), "decrease")
        self.assertEqual(artifact_module._direction(Decimal("0")), "unchanged")
        self.assertEqual(primitive_module._amount_text(Decimal("-0")), "0")


if __name__ == "__main__":
    unittest.main()
