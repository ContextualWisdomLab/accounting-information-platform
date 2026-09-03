"""Buyer-visible financial-report artifact and XBRL export contract tests."""

from __future__ import annotations

import copy
import unittest
import xml.etree.ElementTree as element_tree
from decimal import Decimal

from accounting_information_platform import (
    FinancialReportContext,
    XbrlConceptMapping,
    XbrlTaxonomyProfile,
    build_financial_report_artifact,
    export_xbrl_instance,
)
from accounting_information_platform.financial_reporting import xbrl as xbrl_module
from financial_reporting_fixtures import (
    _report_context,
    _statement_package,
    _taxonomy_profile,
)


class FinancialReportingContractTests(unittest.TestCase):
    """Verify the buyer-visible report and XBRL contract on tied statements."""

    def test_artifact_and_xbrl_are_deterministic_and_evidence_bound(self) -> None:
        """Generate exact current/comparative facts and byte-stable XBRL output."""
        statement_package = _statement_package()
        original_package = copy.deepcopy(statement_package)
        report_artifact = build_financial_report_artifact(
            statement_package,
            _report_context(),
        )
        repeated_artifact = build_financial_report_artifact(
            statement_package,
            _report_context(),
        )
        self.assertEqual(report_artifact, repeated_artifact)
        self.assertEqual(statement_package, original_package)
        self.assertEqual(report_artifact["report_contract_version"], 1)
        self.assertRegex(
            str(report_artifact["source_package_hash"]),
            r"^sha256:[0-9a-f]{64}$",
        )
        self.assertEqual(
            report_artifact["source_snapshot_references"],
            ["snapshot-current", "snapshot-comparison"],
        )
        self.assertEqual(
            report_artifact["profit_and_loss_summary"],
            {
                "revenue_amount": "1200.50",
                "expense_amount": "200.25",
                "net_income_amount": "1000.25",
                "comparison_revenue_amount": "1000.00",
                "comparison_expense_amount": "200.00",
                "comparison_net_income_amount": "800.00",
                "net_income_change_amount": "200.25",
                "net_income_direction_code": "increase",
            },
        )
        fact_index = {
            (fact["fact_code"], fact["period_context_code"]): fact
            for fact in report_artifact["fact_records"]
        }
        self.assertEqual(
            fact_index[("profit_loss.revenue_amount", "current")],
            {
                "fact_code": "profit_loss.revenue_amount",
                "fact_amount": "1200.50",
                "period_context_code": "current",
                "statement_type_code": "income_statement",
                "period_type_code": "duration",
                "source_evidence_paths": ["income_statement.statement_lines[0]"],
            },
        )
        self.assertEqual(
            fact_index[("cash_flow.closing_cash_amount", "comparison")][
                "fact_amount"
            ],
            "1300.00",
        )
        explanation_index = {
            explanation["explanation_code"]: explanation
            for explanation in report_artifact["explanation_records"]
        }
        self.assertEqual(
            explanation_index["profit_loss.net_income_change"]["parameter_map"],
            {
                "current_net_income_amount": "1000.25",
                "comparison_net_income_amount": "800.00",
                "change_amount": "200.25",
            },
        )
        for explanation_code in (
            "financial_position.equation",
            "changes_in_equity.rollforward",
            "cash_flow.rollforward",
        ):
            explanation_record = explanation_index[explanation_code]
            self.assertEqual(explanation_record["status_code"], "balanced")
            self.assertEqual(
                Decimal(explanation_record["parameter_map"]["difference_amount"]),
                Decimal("0"),
            )

        first_export = export_xbrl_instance(
            report_artifact,
            _taxonomy_profile(),
        )
        second_export = export_xbrl_instance(
            report_artifact,
            _taxonomy_profile(),
        )
        self.assertEqual(first_export, second_export)
        self.assertEqual(first_export["media_type"], "application/xbrl+xml")
        self.assertRegex(
            str(first_export["xbrl_instance_hash"]),
            r"^sha256:[0-9a-f]{64}$",
        )
        xml_root = element_tree.fromstring(first_export["xbrl_instance"])
        contexts = {
            context.attrib["id"]
            for context in xml_root.findall(
                f"{{{xbrl_module._XBRLI_NAMESPACE}}}context"
            )
        }
        self.assertEqual(
            contexts,
            {
                "current_duration",
                "current_instant",
                "comparison_duration",
                "comparison_instant",
            },
        )
        taxonomy_namespace = _taxonomy_profile().taxonomy_namespace_uri
        self.assertEqual(
            {
                (fact.attrib["contextRef"], fact.text)
                for fact in xml_root.findall(
                    f"{{{taxonomy_namespace}}}ProfitLoss"
                )
            },
            {
                ("current_duration", "1000.25"),
                ("comparison_duration", "800.00"),
            },
        )
        self.assertEqual(
            {
                (fact.attrib["contextRef"], fact.text)
                for fact in xml_root.findall(f"{{{taxonomy_namespace}}}Assets")
            },
            {
                ("current_instant", "1500.00"),
                ("comparison_instant", "1300.00"),
            },
        )

    def test_package_root_exports_the_reporting_api(self) -> None:
        """Keep reporting consumers on the supported package surface."""
        import accounting_information_platform as package_api

        self.assertIs(package_api.FinancialReportContext, FinancialReportContext)
        self.assertIs(package_api.XbrlConceptMapping, XbrlConceptMapping)
        self.assertIs(package_api.XbrlTaxonomyProfile, XbrlTaxonomyProfile)
        self.assertIs(
            package_api.build_financial_report_artifact,
            build_financial_report_artifact,
        )
        self.assertIs(package_api.export_xbrl_instance, export_xbrl_instance)


if __name__ == "__main__":
    unittest.main()
