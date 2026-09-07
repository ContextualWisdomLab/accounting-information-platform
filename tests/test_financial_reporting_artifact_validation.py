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
    """Exercise artifact inputs, identities, line shapes, and statement controls."""

    def assert_invalid_package(self, package: dict[str, object]) -> None:
        """Require one modified statement package to fail closed."""
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(package, _report_context())

    def test_input_and_json_boundaries(self) -> None:
        """Reject non-mapping inputs, wrong context types, and non-JSON values."""
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact([], _report_context())
        with self.assertRaises(AccountingValidationError):
            reporting.build_financial_report_artifact(_statement_package(), object())
        invalid_package = copy.deepcopy(_statement_package())
        invalid_package["not_json"] = {1, 2}
        self.assert_invalid_package(invalid_package)

    def test_noncomparative_artifact_omits_comparison_and_optional_scope(self) -> None:
        """Generate current-only facts and contexts when no comparison was requested."""
        package = _package_without_comparison()
        artifact = reporting.build_financial_report_artifact(
            package,
            _context_without_comparison(),
        )
        self.assertNotIn("comparison_fiscal_period_reference", artifact)
        self.assertNotIn(
            "net_income_change_amount",
            artifact["profit_and_loss_summary"],
        )
        self.assertEqual(
            [record["explanation_code"] for record in artifact["explanation_records"]],
            [
                "profit_loss.current_summary",
                "financial_position.equation",
                "changes_in_equity.rollforward",
                "cash_flow.rollforward",
            ],
        )
        exported = reporting.export_xbrl_instance(artifact, _valid_profile())
        root = element_tree.fromstring(exported["xbrl_instance"])
        self.assertEqual(
            {
                context.attrib["id"]
                for context in root.findall(
                    f"{{{xbrl_module._XBRLI_NAMESPACE}}}context"
                )
            },
            {"current_duration", "current_instant"},
        )

        package.pop("statement_scope_code")
        for statement_type in statement_module._STATEMENT_TYPES:
            package[statement_type].pop("statement_scope_code")
        no_scope_artifact = reporting.build_financial_report_artifact(
            package,
            _context_without_comparison(),
        )
        self.assertNotIn("statement_scope_code", no_scope_artifact)

    def test_comparison_and_statement_identity_must_be_complete(self) -> None:
        """Reject partial comparisons and any statement torn from package identity."""
        partial = copy.deepcopy(_statement_package())
        partial["cash_flow"].pop("comparison_statement_lines")
        self.assert_invalid_package(partial)

        missing_reference = copy.deepcopy(_statement_package())
        missing_reference.pop("comparison_fiscal_period_reference")
        for statement_type in statement_module._STATEMENT_TYPES:
            missing_reference[statement_type].pop(
                "comparison_fiscal_period_reference"
            )
        self.assert_invalid_package(missing_reference)

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

        for statement_type, field_name, field_value in (
            ("", "cash_flow", None),
            ("cash_flow", "statement_type_code", "income_statement"),
            ("cash_flow", "tenant_reference", "other"),
            ("cash_flow", "statement_scope_code", "period"),
            ("cash_flow", "comparison_fiscal_period_reference", "other"),
            ("cash_flow", "book_reference", "other"),
        ):
            invalid_package = copy.deepcopy(_statement_package())
            if not statement_type:
                invalid_package.pop(field_name)
            else:
                invalid_package[statement_type][field_name] = field_value
            self.assert_invalid_package(invalid_package)

    def test_book_alias_fallback_and_conflict(self) -> None:
        """Use one compatible book alias and reject conflicting aliases."""
        fallback_package = copy.deepcopy(_statement_package())
        fallback_package.pop("book_reference")
        for statement_type in statement_module._STATEMENT_TYPES:
            fallback_package[statement_type].pop("book_reference")
        self.assertEqual(
            reporting.build_financial_report_artifact(
                fallback_package,
                _report_context(),
            )["book_reference"],
            "primary-book",
        )
        conflicting_package = copy.deepcopy(_statement_package())
        conflicting_package["accounting_book_reference"] = "other"
        self.assert_invalid_package(conflicting_package)

    def test_line_shapes_totals_and_income_equation(self) -> None:
        """Reject malformed rows, signs, two-sided lines, totals, and income."""
        invalid_packages: list[dict[str, object]] = []
        for line_value in ("bad", [1]):
            package = copy.deepcopy(_statement_package())
            package["income_statement"]["statement_lines"] = line_value
            invalid_packages.append(package)
        for field_name, field_value in (
            ("account_role_code", "Bad Code"),
            ("account_class_code", "unknown"),
            ("chart_account_code", 1),
            ("chart_account_code", " bad"),
            ("debit_amount", "-1"),
        ):
            package = copy.deepcopy(_statement_package())
            package["income_statement"]["statement_lines"][0][field_name] = field_value
            invalid_packages.append(package)
        two_sided = copy.deepcopy(_statement_package())
        two_sided["income_statement"]["statement_lines"][0]["debit_amount"] = "1"
        invalid_packages.append(two_sided)
        wrong_total = copy.deepcopy(_statement_package())
        wrong_total["income_statement"]["total_credit_amount"] = "0"
        invalid_packages.append(wrong_total)
        wrong_income = copy.deepcopy(_statement_package())
        wrong_income["income_statement"]["net_income_amount"] = "999"
        invalid_packages.append(wrong_income)
        for invalid_package in invalid_packages:
            self.assert_invalid_package(invalid_package)

    def test_financial_position_equity_and_cash_controls(self) -> None:
        """Reject every broken balance, rollforward, cross-statement, and cash tie."""
        invalid_packages: list[dict[str, object]] = []

        package = copy.deepcopy(_statement_package())
        package["balance_sheet"]["statement_lines"][0]["debit_amount"] = "1600.00"
        package["balance_sheet"]["total_debit_amount"] = "1600.00"
        invalid_packages.append(package)

        for line_index, line_amount, closing_amount, total_amount in (
            (2, "90.00", None, "2390.25"),
            (1, "999.00", "1198.75", "2397.50"),
            (2, "100.75", "1201.00", "2402.00"),
        ):
            package = copy.deepcopy(_statement_package())
            package["changes_in_equity"]["statement_lines"][line_index][
                "credit_amount"
            ] = line_amount
            if closing_amount is not None:
                package["changes_in_equity"]["statement_lines"][3][
                    "credit_amount"
                ] = closing_amount
            package["changes_in_equity"]["total_credit_amount"] = total_amount
            invalid_packages.append(package)

        for line_index in (2, 5, 7):
            package = copy.deepcopy(_statement_package())
            package["cash_flow"]["statement_lines"][line_index][
                "credit_amount"
            ] = "999.00" if line_index != 7 else "1499.00"
            package["cash_flow"]["total_credit_amount"] = "4999.25"
            invalid_packages.append(package)

        package = copy.deepcopy(_statement_package())
        package["cash_flow"]["statement_lines"][0]["credit_amount"] = "999.00"
        package["cash_flow"]["statement_lines"][1]["debit_amount"] = "0"
        package["cash_flow"]["statement_lines"][1]["credit_amount"] = "1.00"
        package["cash_flow"]["total_debit_amount"] = "0"
        package["cash_flow"]["total_credit_amount"] = "5000.00"
        invalid_packages.append(package)

        package = copy.deepcopy(_statement_package())
        package["balance_sheet"]["statement_lines"][0]["debit_amount"] = "1400.00"
        package["balance_sheet"]["statement_lines"].append(
            {
                "chart_account_code": "110300",
                "account_role_code": "other_asset",
                "account_class_code": "asset",
                "debit_amount": "100.00",
                "credit_amount": "0",
            }
        )
        invalid_packages.append(package)

        for invalid_package in invalid_packages:
            self.assert_invalid_package(invalid_package)

        no_cash_role = copy.deepcopy(_statement_package())
        no_cash_role["balance_sheet"]["statement_lines"][0][
            "account_role_code"
        ] = "other_cash"
        self.assertEqual(
            reporting.build_financial_report_artifact(
                no_cash_role,
                _report_context(),
            )["profit_and_loss_summary"]["net_income_amount"],
            "1000.25",
        )

    def test_rollforward_roles_evidence_fallbacks_and_directions(self) -> None:
        """Reject invalid roles and cover evidence and movement fallbacks."""
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
        for package in (unexpected_role, duplicate_role, missing_role):
            self.assert_invalid_package(package)

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
        self.assertEqual(artifact_module._direction(Decimal("-1")), "decrease")
        self.assertEqual(artifact_module._direction(Decimal("0")), "unchanged")
        self.assertEqual(primitive_module._amount_text(Decimal("-0")), "0")


if __name__ == "__main__":
    unittest.main()
