"""Reusable fixtures for financial-reporting contract tests."""

from __future__ import annotations

import copy
from datetime import date

from accounting_information_platform import (
    FinancialReportContext,
    XbrlConceptMapping,
    XbrlTaxonomyProfile,
)
from accounting_information_platform.financial_reporting import primitives as primitive_module
from accounting_information_platform.financial_reporting import statements as statement_module


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


def _statement_package() -> dict[str, object]:
    """Return current and comparative statements whose controls tie."""
    common = {
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
        **common,
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
        **common,
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
        **common,
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
        **common,
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


def _report_context() -> FinancialReportContext:
    """Return complete current and comparative reporting context."""
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
    """Return a synthetic taxonomy profile that makes no filing claim."""
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
            XbrlConceptMapping("profit_loss.revenue_amount", "Revenue", "duration"),
            XbrlConceptMapping("profit_loss.expense_amount", "Expense", "duration"),
            XbrlConceptMapping("profit_loss.net_income_amount", "ProfitLoss", "duration"),
            XbrlConceptMapping("financial_position.asset_amount", "Assets", "instant"),
            XbrlConceptMapping(
                "financial_position.liability_amount",
                "Liabilities",
                "instant",
            ),
            XbrlConceptMapping("financial_position.equity_amount", "Equity", "instant"),
            XbrlConceptMapping(
                "cash_flow.closing_cash_amount",
                "CashAndCashEquivalents",
                "instant",
            ),
        ),
    )


def _context_without_comparison() -> FinancialReportContext:
    """Return reporting context with no comparative period."""
    return FinancialReportContext(
        "https://example.com/entity",
        "ENTITY-1",
        "KRW",
        date(2026, 1, 1),
        date(2026, 12, 31),
        decimal_precision=0,
    )


def _package_without_comparison() -> dict[str, object]:
    """Return a statement package with every comparative field removed."""
    package = copy.deepcopy(_statement_package())
    package.pop("comparison_fiscal_period_reference")
    for statement_name in statement_module._STATEMENT_TYPES:
        statement = package[statement_name]
        statement.pop("comparison_fiscal_period_reference")
        statement.pop("comparison_snapshot_record_id")
        for statement_key in list(statement):
            if statement_key.startswith("comparison_"):
                statement.pop(statement_key)
    return package


def _valid_mapping(**overrides: object) -> XbrlConceptMapping:
    """Return one valid concept mapping with optional test overrides."""
    mapping_values = {
        "fact_code": "profit_loss.net_income_amount",
        "concept_local_name": "ProfitLoss",
        "period_type_code": "duration",
    }
    mapping_values.update(overrides)
    return XbrlConceptMapping(**mapping_values)


def _valid_profile(**overrides: object) -> XbrlTaxonomyProfile:
    """Return one valid synthetic taxonomy profile with optional overrides."""
    profile_values = {
        "profile_identifier": "profile-1",
        "profile_version": 1,
        "reporting_standard_code": "test_gaap",
        "taxonomy_release_code": "2026",
        "taxonomy_prefix": "testgaap",
        "taxonomy_namespace_uri": "https://example.com/taxonomy",
        "schema_reference_uri": "https://example.com/taxonomy/entry.xsd",
        "taxonomy_package_hash": "sha256:" + "a" * 64,
        "concept_mappings": (_valid_mapping(),),
    }
    profile_values.update(overrides)
    return XbrlTaxonomyProfile(**profile_values)


def _rehash(artifact: dict[str, object]) -> dict[str, object]:
    """Recompute an artifact hash after a controlled test mutation."""
    hash_document = dict(artifact)
    hash_document.pop("report_artifact_hash", None)
    artifact["report_artifact_hash"] = primitive_module._digest(
        primitive_module._json_bytes(hash_document, "invalid test artifact")
    )
    return artifact
