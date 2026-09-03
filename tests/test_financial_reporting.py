"""Financial-report artifact and XBRL export contract tests."""

from __future__ import annotations

import copy
import unittest
import xml.etree.ElementTree as element_tree
from datetime import date
from decimal import Decimal

from accounting_information_platform import (
    FinancialReportContext,
    XbrlConceptMapping,
    XbrlTaxonomyProfile,
    build_financial_report_artifact,
    export_xbrl_instance,
)
from accounting_information_platform.core import AccountingValidationError


class FinancialReportingTests(unittest.TestCase):
    """Prove deterministic reporting without introducing a second numerical truth."""

    def setUp(self) -> None:
        """Create one tied current-and-comparative financial-statement package."""
        self.statement_package = _statement_package()
        self.report_context = _report_context()
        self.taxonomy_profile = _taxonomy_profile()

    def test_artifact_is_deterministic_and_preserves_exact_fact_evidence(self) -> None:
        """The same authoritative package and context must produce the same artifact."""
        original_package = copy.deepcopy(self.statement_package)

        first_artifact = build_financial_report_artifact(
            self.statement_package, self.report_context
        )
        second_artifact = build_financial_report_artifact(
            self.statement_package, self.report_context
        )

        self.assertEqual(first_artifact, second_artifact)
        self.assertEqual(self.statement_package, original_package)
        self.assertEqual(first_artifact["report_contract_version"], 1)
        self.assertTrue(
            str(first_artifact["report_artifact_reference"]).startswith(
                "urn:cwl:accounting:financial_report:"
            )
        )
        self.assertRegex(
            str(first_artifact["source_package_hash"]), r"^sha256:[0-9a-f]{64}$"
        )
        self.assertEqual(
            first_artifact["source_snapshot_references"],
            ["snapshot-current", "snapshot-comparison"],
        )
        self.assertEqual(
            first_artifact["profit_and_loss_summary"],
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
            (str(fact_record["fact_code"]), str(fact_record["period_context_code"])): fact_record
            for fact_record in first_artifact["fact_records"]
        }
        revenue_fact = fact_index[("profit_loss.revenue_amount", "current")]
        self.assertEqual(revenue_fact["fact_amount"], "1200.50")
        self.assertEqual(
            revenue_fact["source_evidence_paths"],
            ["income_statement.statement_lines[0]"],
        )
        comparison_cash_fact = fact_index[("cash_flow.closing_cash_amount", "comparison")]
        self.assertEqual(comparison_cash_fact["fact_amount"], "1300.00")
        self.assertEqual(
            comparison_cash_fact["source_evidence_paths"],
            ["cash_flow.comparison_statement_lines[7]"],
        )
        explanation_codes = {
            str(explanation_record["explanation_code"])
            for explanation_record in first_artifact["explanation_records"]
        }
        self.assertEqual(
            explanation_codes,
            {
                "profit_loss.current_summary",
                "profit_loss.net_income_change",
                "financial_position.equation",
                "changes_in_equity.rollforward",
                "cash_flow.rollforward",
            },
        )

    def test_explanations_retain_exact_parameters_and_control_status(self) -> None:
        """Explanation records must be machine-readable and tied to source paths."""
        report_artifact = build_financial_report_artifact(
            self.statement_package, self.report_context
        )
        explanation_index = {
            str(explanation_record["explanation_code"]): explanation_record
            for explanation_record in report_artifact["explanation_records"]
        }

        movement_record = explanation_index["profit_loss.net_income_change"]
        self.assertEqual(movement_record["direction_code"], "increase")
        self.assertEqual(
            movement_record["parameter_map"],
            {
                "current_net_income_amount": "1000.25",
                "comparison_net_income_amount": "800.00",
                "change_amount": "200.25",
            },
        )
        self.assertEqual(
            movement_record["source_evidence_paths"],
            [
                "income_statement.net_income_amount",
                "income_statement.comparison_net_income_amount",
            ],
        )
        for control_code in (
            "financial_position.equation",
            "changes_in_equity.rollforward",
            "cash_flow.rollforward",
        ):
            self.assertEqual(explanation_index[control_code]["status_code"], "balanced")
            self.assertEqual(
                Decimal(str(explanation_index[control_code]["parameter_map"]["difference_amount"])),
                Decimal("0"),
            )

    def test_artifact_fails_closed_for_invalid_statement_packages(self) -> None:
        """Missing, malformed, inconsistent, or identity-torn statements must be rejected."""
        invalid_packages: list[tuple[str, dict[str, object]]] = []

        missing_statement = copy.deepcopy(self.statement_package)
        del missing_statement["cash_flow"]
        invalid_packages.append(("required financial statement cash_flow is missing", missing_statement))

        malformed_amount = copy.deepcopy(self.statement_package)
        malformed_amount["income_statement"]["statement_lines"][0]["credit_amount"] = "NaN"
        invalid_packages.append(("must be a finite decimal", malformed_amount))

        unknown_class = copy.deepcopy(self.statement_package)
        unknown_class["income_statement"]["statement_lines"][0]["account_class_code"] = "unknown"
        invalid_packages.append(("account_class_code", unknown_class))

        broken_income = copy.deepcopy(self.statement_package)
        broken_income["income_statement"]["net_income_amount"] = "999.00"
        invalid_packages.append(("income statement does not reproduce net_income_amount", broken_income))

        broken_position = copy.deepcopy(self.statement_package)
        broken_position["balance_sheet"]["statement_lines"][0]["debit_amount"] = "1600.00"
        invalid_packages.append(("statement of financial position does not balance", broken_position))

        torn_identity = copy.deepcopy(self.statement_package)
        torn_identity["cash_flow"]["fiscal_period_reference"] = (
            "urn:cwl:accounting:fiscal_period:2026-11"
        )
        invalid_packages.append(("financial statement identity does not match package", torn_identity))

        for expected_message, invalid_package in invalid_packages:
            with self.subTest(expected_message=expected_message):
                with self.assertRaisesRegex(AccountingValidationError, expected_message):
                    build_financial_report_artifact(invalid_package, self.report_context)

    def test_comparative_statement_requires_complete_comparison_context(self) -> None:
        """Comparative facts cannot be exported without their actual date range."""
        context_without_comparison = FinancialReportContext(
            entity_identifier_scheme=self.report_context.entity_identifier_scheme,
            entity_identifier_value=self.report_context.entity_identifier_value,
            reporting_currency_code="KRW",
            current_period_start_date=date(2026, 1, 1),
            current_period_end_date=date(2026, 12, 31),
            decimal_precision=2,
        )
        with self.assertRaisesRegex(
            AccountingValidationError, "comparison period dates are required"
        ):
            build_financial_report_artifact(
                self.statement_package, context_without_comparison
            )

    def test_report_context_rejects_invalid_identity_currency_and_periods(self) -> None:
        """The report context must be complete enough to create XBRL contexts and units."""
        invalid_arguments = (
            {
                "entity_identifier_scheme": "relative-scheme",
                "entity_identifier_value": "ENTITY-1",
            },
            {
                "entity_identifier_scheme": "https://example.com/entity",
                "entity_identifier_value": " ENTITY-1",
            },
            {
                "reporting_currency_code": "krw",
            },
            {
                "current_period_start_date": date(2026, 12, 31),
                "current_period_end_date": date(2026, 1, 1),
            },
            {
                "comparison_period_start_date": date(2025, 1, 1),
                "comparison_period_end_date": None,
            },
            {
                "comparison_period_start_date": date(2025, 12, 31),
                "comparison_period_end_date": date(2025, 1, 1),
            },
            {
                "decimal_precision": 19,
            },
        )
        for overrides in invalid_arguments:
            arguments = {
                "entity_identifier_scheme": "https://example.com/entity",
                "entity_identifier_value": "ENTITY-1",
                "reporting_currency_code": "KRW",
                "current_period_start_date": date(2026, 1, 1),
                "current_period_end_date": date(2026, 12, 31),
                "comparison_period_start_date": date(2025, 1, 1),
                "comparison_period_end_date": date(2025, 12, 31),
                "decimal_precision": 2,
            }
            arguments.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(AccountingValidationError):
                    FinancialReportContext(**arguments)

    def test_taxonomy_profile_rejects_ambiguous_or_unverifiable_mappings(self) -> None:
        """A taxonomy profile must be versioned, addressable, hashed, and one-to-one."""
        valid_mapping = XbrlConceptMapping(
            fact_code="profit_loss.net_income_amount",
            concept_local_name="ProfitLoss",
            period_type_code="duration",
        )
        invalid_builders = (
            lambda: XbrlConceptMapping(
                fact_code="bad fact",
                concept_local_name="ProfitLoss",
                period_type_code="duration",
            ),
            lambda: XbrlConceptMapping(
                fact_code="profit_loss.net_income_amount",
                concept_local_name="bad:concept",
                period_type_code="duration",
            ),
            lambda: XbrlConceptMapping(
                fact_code="profit_loss.net_income_amount",
                concept_local_name="ProfitLoss",
                period_type_code="quarter",
            ),
            lambda: XbrlTaxonomyProfile(
                profile_identifier="",
                profile_version=1,
                reporting_standard_code="test_gaap",
                taxonomy_release_code="2026",
                taxonomy_prefix="testgaap",
                taxonomy_namespace_uri="https://example.com/taxonomy/2026",
                schema_reference_uri="https://example.com/taxonomy/2026/entry.xsd",
                taxonomy_package_hash="sha256:" + "a" * 64,
                concept_mappings=(valid_mapping,),
            ),
            lambda: XbrlTaxonomyProfile(
                profile_identifier="test-gaap-2026",
                profile_version=0,
                reporting_standard_code="test_gaap",
                taxonomy_release_code="2026",
                taxonomy_prefix="1bad",
                taxonomy_namespace_uri="relative-namespace",
                schema_reference_uri="relative-entry.xsd",
                taxonomy_package_hash="bad-hash",
                concept_mappings=(valid_mapping,),
            ),
            lambda: XbrlTaxonomyProfile(
                profile_identifier="test-gaap-2026",
                profile_version=1,
                reporting_standard_code="test_gaap",
                taxonomy_release_code="2026",
                taxonomy_prefix="testgaap",
                taxonomy_namespace_uri="https://example.com/taxonomy/2026",
                schema_reference_uri="https://example.com/taxonomy/2026/entry.xsd",
                taxonomy_package_hash="sha256:" + "a" * 64,
                concept_mappings=(valid_mapping, valid_mapping),
            ),
            lambda: XbrlTaxonomyProfile(
                profile_identifier="test-gaap-2026",
                profile_version=1,
                reporting_standard_code="test_gaap",
                taxonomy_release_code="2026",
                taxonomy_prefix="testgaap",
                taxonomy_namespace_uri="https://example.com/taxonomy/2026",
                schema_reference_uri="https://example.com/taxonomy/2026/entry.xsd",
                taxonomy_package_hash="sha256:" + "a" * 64,
                concept_mappings=(
                    valid_mapping,
                    XbrlConceptMapping(
                        fact_code="profit_loss.revenue_amount",
                        concept_local_name="ProfitLoss",
                        period_type_code="duration",
                    ),
                ),
            ),
        )
        for invalid_builder in invalid_builders:
            with self.subTest(invalid_builder=invalid_builder):
                with self.assertRaises(AccountingValidationError):
                    invalid_builder()

    def test_xbrl_export_is_deterministic_and_contains_current_and_comparative_facts(self) -> None:
        """The supplied profile must create one deterministic, provenance-bound XBRL instance."""
        report_artifact = build_financial_report_artifact(
            self.statement_package, self.report_context
        )

        first_export = export_xbrl_instance(report_artifact, self.taxonomy_profile)
        second_export = export_xbrl_instance(report_artifact, self.taxonomy_profile)

        self.assertEqual(first_export, second_export)
        self.assertEqual(first_export["export_contract_version"], 1)
        self.assertEqual(first_export["media_type"], "application/xbrl+xml")
        self.assertRegex(
            str(first_export["xbrl_instance_hash"]), r"^sha256:[0-9a-f]{64}$"
        )
        self.assertEqual(
            first_export["taxonomy_package_hash"], self.taxonomy_profile.taxonomy_package_hash
        )
        xml_text = str(first_export["xbrl_instance"])
        self.assertTrue(xml_text.startswith("<?xml version='1.0' encoding='utf-8'?>"))
        xml_root = element_tree.fromstring(xml_text)
        xbrli_namespace = "http://www.xbrl.org/2003/instance"
        link_namespace = "http://www.xbrl.org/2003/linkbase"
        xlink_namespace = "http://www.w3.org/1999/xlink"
        self.assertEqual(xml_root.tag, f"{{{xbrli_namespace}}}xbrl")
        context_ids = {
            str(context_element.attrib["id"])
            for context_element in xml_root.findall(f"{{{xbrli_namespace}}}context")
        }
        self.assertEqual(
            context_ids,
            {
                "current_duration",
                "current_instant",
                "comparison_duration",
                "comparison_instant",
            },
        )
        schema_reference = xml_root.find(f"{{{link_namespace}}}schemaRef")
        self.assertIsNotNone(schema_reference)
        self.assertEqual(
            schema_reference.attrib[f"{{{xlink_namespace}}}href"],
            self.taxonomy_profile.schema_reference_uri,
        )
        unit_measure = xml_root.find(
            f"{{{xbrli_namespace}}}unit/{{{xbrli_namespace}}}measure"
        )
        self.assertIsNotNone(unit_measure)
        self.assertEqual(unit_measure.text, "iso4217:KRW")
        taxonomy_namespace = self.taxonomy_profile.taxonomy_namespace_uri
        profit_facts = xml_root.findall(f"{{{taxonomy_namespace}}}ProfitLoss")
        self.assertEqual(
            {(fact.attrib["contextRef"], fact.text) for fact in profit_facts},
            {
                ("current_duration", "1000.25"),
                ("comparison_duration", "800.00"),
            },
        )
        asset_facts = xml_root.findall(f"{{{taxonomy_namespace}}}Assets")
        self.assertEqual(
            {(fact.attrib["contextRef"], fact.text) for fact in asset_facts},
            {
                ("current_instant", "1500.00"),
                ("comparison_instant", "1300.00"),
            },
        )
        for fact_element in profit_facts + asset_facts:
            self.assertEqual(fact_element.attrib["unitRef"], "reporting_currency")
            self.assertEqual(fact_element.attrib["decimals"], "2")

    def test_xbrl_export_rejects_missing_facts_and_tampered_artifacts(self) -> None:
        """Mappings and artifact hashes must be checked before any instance is returned."""
        report_artifact = build_financial_report_artifact(
            self.statement_package, self.report_context
        )
        missing_fact_profile = XbrlTaxonomyProfile(
            profile_identifier="test-gaap-missing",
            profile_version=1,
            reporting_standard_code="test_gaap",
            taxonomy_release_code="2026",
            taxonomy_prefix="testgaap",
            taxonomy_namespace_uri="https://example.com/taxonomy/2026",
            schema_reference_uri="https://example.com/taxonomy/2026/entry.xsd",
            taxonomy_package_hash="sha256:" + "b" * 64,
            concept_mappings=(
                XbrlConceptMapping(
                    fact_code="profit_loss.missing_amount",
                    concept_local_name="MissingAmount",
                    period_type_code="duration",
                ),
            ),
        )
        with self.assertRaisesRegex(AccountingValidationError, "mapped fact is missing"):
            export_xbrl_instance(report_artifact, missing_fact_profile)

        tampered_artifact = copy.deepcopy(report_artifact)
        tampered_artifact["source_statement_package"]["tenant_reference"] = "tenant-tampered"
        with self.assertRaisesRegex(AccountingValidationError, "source package hash"):
            export_xbrl_instance(tampered_artifact, self.taxonomy_profile)

    def test_package_root_exports_the_reporting_api(self) -> None:
        """Consumers must not import private implementation paths for reporting."""
        import accounting_information_platform as package_api

        self.assertIs(package_api.FinancialReportContext, FinancialReportContext)
        self.assertIs(package_api.XbrlConceptMapping, XbrlConceptMapping)
        self.assertIs(package_api.XbrlTaxonomyProfile, XbrlTaxonomyProfile)
        self.assertIs(
            package_api.build_financial_report_artifact,
            build_financial_report_artifact,
        )
        self.assertIs(package_api.export_xbrl_instance, export_xbrl_instance)


def _statement_package() -> dict[str, object]:
    """Return a four-statement package whose current and comparison values tie."""
    common_current = {
        "tenant_reference": "tenant-a",
        "legal_entity_reference": "entity-a",
        "accounting_book_reference": "primary-book",
        "book_reference": "primary-book",
        "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-12",
        "comparison_fiscal_period_reference": (
            "urn:cwl:accounting:fiscal_period:2025-12"
        ),
        "statement_scope_code": "year_to_date",
        "snapshot_record_id": "snapshot-current",
        "comparison_snapshot_record_id": "snapshot-comparison",
    }
    income_statement = {
        **common_current,
        "statement_type_code": "income_statement",
        "statement_lines": [
            _statement_line("410100", "usage_revenue", "revenue", "0", "1200.50"),
            _statement_line("510100", "write_off_expense", "expense", "200.25", "0"),
        ],
        "total_debit_amount": "200.25",
        "total_credit_amount": "1200.50",
        "net_income_amount": "1000.25",
        "comparison_statement_lines": [
            _statement_line("410100", "usage_revenue", "revenue", "0", "1000.00"),
            _statement_line("510100", "write_off_expense", "expense", "200.00", "0"),
        ],
        "comparison_total_debit_amount": "200.00",
        "comparison_total_credit_amount": "1000.00",
        "comparison_net_income_amount": "800.00",
    }
    balance_sheet = {
        **common_current,
        "statement_type_code": "balance_sheet",
        "statement_lines": [
            _statement_line("110200", "cash_receipt", "asset", "1500.00", "0"),
            _statement_line("210100", "tax_payable", "liability", "0", "300.00"),
            _statement_line("310100", "retained_earnings", "equity", "0", "199.75"),
        ],
        "total_debit_amount": "1500.00",
        "total_credit_amount": "499.75",
        "net_income_amount": "1000.25",
        "comparison_statement_lines": [
            _statement_line("110200", "cash_receipt", "asset", "1300.00", "0"),
            _statement_line("210100", "tax_payable", "liability", "0", "250.00"),
            _statement_line("310100", "retained_earnings", "equity", "0", "250.00"),
        ],
        "comparison_total_debit_amount": "1300.00",
        "comparison_total_credit_amount": "500.00",
        "comparison_net_income_amount": "800.00",
    }
    changes_in_equity = {
        **common_current,
        "statement_type_code": "changes_in_equity",
        "statement_lines": [
            _statement_line("", "opening_equity", "equity", "0", "100.00"),
            _statement_line("", "period_net_income", "equity", "0", "1000.25"),
            _statement_line("", "other_equity_movements", "equity", "0", "99.75"),
            _statement_line("", "closing_equity", "equity", "0", "1200.00"),
        ],
        "total_debit_amount": "0",
        "total_credit_amount": "2400.00",
        "net_income_amount": "1000.25",
        "comparison_statement_lines": [
            _statement_line("", "opening_equity", "equity", "0", "100.00"),
            _statement_line("", "period_net_income", "equity", "0", "800.00"),
            _statement_line("", "other_equity_movements", "equity", "0", "150.00"),
            _statement_line("", "closing_equity", "equity", "0", "1050.00"),
        ],
        "comparison_total_debit_amount": "0",
        "comparison_total_credit_amount": "2100.00",
        "comparison_net_income_amount": "800.00",
    }
    cash_flow = {
        **common_current,
        "statement_type_code": "cash_flow",
        "statement_lines": [
            _statement_line("", "period_net_income", "", "0", "1000.25"),
            _statement_line("", "operating_working_capital", "", "0.25", "0"),
            _statement_line("", "cash_from_operations", "", "0", "1000.00"),
            _statement_line("", "cash_from_investing", "", "0", "0"),
            _statement_line("", "cash_from_financing", "", "0", "0"),
            _statement_line("", "net_cash_change", "", "0", "1000.00"),
            _statement_line("", "opening_cash", "", "0", "500.00"),
            _statement_line("", "closing_cash", "", "0", "1500.00"),
        ],
        "total_debit_amount": "0.25",
        "total_credit_amount": "5000.25",
        "net_income_amount": "1000.25",
        "comparison_statement_lines": [
            _statement_line("", "period_net_income", "", "0", "800.00"),
            _statement_line("", "operating_working_capital", "", "0", "100.00"),
            _statement_line("", "cash_from_operations", "", "0", "900.00"),
            _statement_line("", "cash_from_investing", "", "0", "0"),
            _statement_line("", "cash_from_financing", "", "0", "0"),
            _statement_line("", "net_cash_change", "", "0", "900.00"),
            _statement_line("", "opening_cash", "", "0", "400.00"),
            _statement_line("", "closing_cash", "", "0", "1300.00"),
        ],
        "comparison_total_debit_amount": "0",
        "comparison_total_credit_amount": "4400.00",
        "comparison_net_income_amount": "800.00",
    }
    return {
        "tenant_reference": "tenant-a",
        "legal_entity_reference": "entity-a",
        "accounting_book_reference": "primary-book",
        "book_reference": "primary-book",
        "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-12",
        "comparison_fiscal_period_reference": (
            "urn:cwl:accounting:fiscal_period:2025-12"
        ),
        "statement_scope_code": "year_to_date",
        "income_statement": income_statement,
        "balance_sheet": balance_sheet,
        "changes_in_equity": changes_in_equity,
        "cash_flow": cash_flow,
    }


def _statement_line(
    chart_account_code: str,
    account_role_code: str,
    account_class_code: str,
    debit_amount: str,
    credit_amount: str,
) -> dict[str, str]:
    """Return one exact-decimal statement line."""
    return {
        "chart_account_code": chart_account_code,
        "account_role_code": account_role_code,
        "account_class_code": account_class_code,
        "debit_amount": debit_amount,
        "credit_amount": credit_amount,
    }


def _report_context() -> FinancialReportContext:
    """Return complete current and comparative XBRL context data."""
    return FinancialReportContext(
        entity_identifier_scheme="https://example.com/entity",
        entity_identifier_value="ENTITY-1",
        reporting_currency_code="KRW",
        current_period_start_date=date(2026, 1, 1),
        current_period_end_date=date(2026, 12, 31),
        comparison_period_start_date=date(2025, 1, 1),
        comparison_period_end_date=date(2025, 12, 31),
        decimal_precision=2,
    )


def _taxonomy_profile() -> XbrlTaxonomyProfile:
    """Return a non-regulatory taxonomy profile for serializer contract testing."""
    return XbrlTaxonomyProfile(
        profile_identifier="test-gaap-2026",
        profile_version=1,
        reporting_standard_code="test_gaap",
        taxonomy_release_code="2026",
        taxonomy_prefix="testgaap",
        taxonomy_namespace_uri="https://example.com/taxonomy/2026",
        schema_reference_uri="https://example.com/taxonomy/2026/entry.xsd",
        taxonomy_package_hash="sha256:" + "a" * 64,
        concept_mappings=(
            XbrlConceptMapping(
                fact_code="profit_loss.revenue_amount",
                concept_local_name="Revenue",
                period_type_code="duration",
            ),
            XbrlConceptMapping(
                fact_code="profit_loss.expense_amount",
                concept_local_name="Expense",
                period_type_code="duration",
            ),
            XbrlConceptMapping(
                fact_code="profit_loss.net_income_amount",
                concept_local_name="ProfitLoss",
                period_type_code="duration",
            ),
            XbrlConceptMapping(
                fact_code="financial_position.asset_amount",
                concept_local_name="Assets",
                period_type_code="instant",
            ),
            XbrlConceptMapping(
                fact_code="financial_position.liability_amount",
                concept_local_name="Liabilities",
                period_type_code="instant",
            ),
            XbrlConceptMapping(
                fact_code="financial_position.equity_amount",
                concept_local_name="Equity",
                period_type_code="instant",
            ),
            XbrlConceptMapping(
                fact_code="cash_flow.closing_cash_amount",
                concept_local_name="CashAndCashEquivalents",
                period_type_code="instant",
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
