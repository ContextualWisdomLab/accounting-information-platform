"""Canonical exact-decimal financial-report proposal construction."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal

from ..core import AccountingValidationError
from .contracts import FinancialReportContext
from .primitives import (
    _amount_text,
    _book_reference,
    _digest,
    _json_bytes,
    _mapping_text,
    _optional_text,
)
from .statements import _STATEMENT_TYPES, _period_report, _statements


def build_financial_report_artifact(
    statement_package: Mapping[str, object],
    report_context: FinancialReportContext,
) -> dict[str, object]:
    """Build a deterministic unverified proposal from caller-supplied statements."""
    if not isinstance(statement_package, Mapping):
        raise AccountingValidationError("statement_package must be a mapping")
    if not isinstance(report_context, FinancialReportContext):
        raise AccountingValidationError(
            "report_context must be a FinancialReportContext"
        )
    source_bytes = _json_bytes(
        statement_package,
        "statement_package is not JSON-compatible",
    )
    source_document = json.loads(source_bytes.decode("utf-8"))
    source_hash = _digest(source_bytes)
    statement_documents = _statements(source_document)
    comparison_reference = _optional_text(
        source_document.get("comparison_fiscal_period_reference"),
        "comparison_fiscal_period_reference",
    )
    comparison_data = all(
        "comparison_statement_lines" in statement_document
        for statement_document in statement_documents.values()
    )
    any_comparison_data = any(
        "comparison_statement_lines" in statement_document
        for statement_document in statement_documents.values()
    )
    if (
        any_comparison_data != bool(comparison_reference)
        or any_comparison_data != comparison_data
    ):
        raise AccountingValidationError(
            "comparison financial statement identity and data must be supplied together"
        )
    comparison_context = report_context.comparison_period_start_date is not None
    if comparison_data != comparison_context:
        if comparison_data:
            raise AccountingValidationError(
                "comparison period dates are required for comparative financial statements"
            )
        raise AccountingValidationError(
            "comparison period dates require comparative financial statement data"
        )

    current_report = _period_report(statement_documents, "current", False)
    comparison_report = (
        _period_report(statement_documents, "comparison", True)
        if comparison_data
        else None
    )
    fact_records = current_report["fact_records"] + (
        comparison_report["fact_records"] if comparison_report else []
    )
    context_document = report_context._document()
    identity_hash = _digest(
        _json_bytes(
            {
                "report_contract_version": 1,
                "truth_status_code": "proposed",
                "source_authority_code": "caller_supplied_statement_package",
                "publication_readiness_code": "unverified",
                "source_package_hash": source_hash,
                "report_context": context_document,
            },
            "report identity is not JSON-compatible",
        )
    )
    artifact_document: dict[str, object] = {
        "report_contract_version": 1,
        "truth_status_code": "proposed",
        "source_authority_code": "caller_supplied_statement_package",
        "publication_readiness_code": "unverified",
        "authoritative_report": False,
        "report_artifact_reference": (
            "urn:cwl:accounting:financial_report_proposal:"
            + identity_hash.split(":", 1)[1]
        ),
        "source_package_hash": source_hash,
        "tenant_reference": _mapping_text(source_document, "tenant_reference"),
        "legal_entity_reference": _mapping_text(
            source_document,
            "legal_entity_reference",
        ),
        "book_reference": _book_reference(source_document),
        "fiscal_period_reference": _mapping_text(
            source_document,
            "fiscal_period_reference",
        ),
        "report_context": context_document,
        "source_snapshot_references": _snapshot_references(statement_documents),
        "profit_and_loss_summary": _profit_loss_summary(
            current_report,
            comparison_report,
        ),
        "fact_records": fact_records,
        "explanation_records": _explanations(
            current_report,
            comparison_report,
        ),
        "source_statement_package": source_document,
    }
    if comparison_reference:
        artifact_document["comparison_fiscal_period_reference"] = (
            comparison_reference
        )
    if "statement_scope_code" in source_document:
        artifact_document["statement_scope_code"] = source_document[
            "statement_scope_code"
        ]
    artifact_document["report_artifact_hash"] = _digest(
        _json_bytes(
            artifact_document,
            "report artifact is not JSON-compatible",
        )
    )
    return artifact_document


def _profit_loss_summary(
    current_report: Mapping[str, object],
    comparison_report: Mapping[str, object] | None,
) -> dict[str, str]:
    """Return current and optional comparative profit-or-loss headline amounts."""
    current_profit = current_report["profit_loss"]
    summary_document = {
        "revenue_amount": _amount_text(current_profit["revenue_amount"]),
        "expense_amount": _amount_text(current_profit["expense_amount"]),
        "net_income_amount": _amount_text(current_profit["net_income_amount"]),
    }
    if comparison_report:
        comparison_profit = comparison_report["profit_loss"]
        change_amount = (
            current_profit["net_income_amount"]
            - comparison_profit["net_income_amount"]
        )
        summary_document.update(
            {
                "comparison_revenue_amount": _amount_text(
                    comparison_profit["revenue_amount"]
                ),
                "comparison_expense_amount": _amount_text(
                    comparison_profit["expense_amount"]
                ),
                "comparison_net_income_amount": _amount_text(
                    comparison_profit["net_income_amount"]
                ),
                "net_income_change_amount": _amount_text(change_amount),
                "net_income_direction_code": _direction(change_amount),
            }
        )
    return summary_document


def _explanations(
    current_report: Mapping[str, object],
    comparison_report: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    """Create structured explanations and cross-statement control results."""
    profit_values = current_report["profit_loss"]
    position_values = current_report["financial_position"]
    equity_values = current_report["equity"]
    cash_values = current_report["cash_flow"]
    position_expected = (
        position_values["liability_amount"]
        + position_values["equity_amount"]
        + position_values["unclosed_net_income_amount"]
    )
    equity_expected = (
        equity_values["opening_equity_amount"]
        + equity_values["period_net_income_amount"]
        + equity_values["other_equity_movements_amount"]
    )
    cash_expected = (
        cash_values["opening_cash_amount"]
        + cash_values["net_cash_change_amount"]
    )
    explanation_records = [
        {
            "explanation_code": "profit_loss.current_summary",
            "status_code": "informational",
            "direction_code": _direction(profit_values["net_income_amount"]),
            "parameter_map": {
                "revenue_amount": _amount_text(
                    profit_values["revenue_amount"]
                ),
                "expense_amount": _amount_text(
                    profit_values["expense_amount"]
                ),
                "net_income_amount": _amount_text(
                    profit_values["net_income_amount"]
                ),
            },
            "source_evidence_paths": [
                "income_statement.statement_lines",
                "income_statement.net_income_amount",
            ],
        },
        _control_record(
            "financial_position.equation",
            {
                "asset_amount": position_values["asset_amount"],
                "liability_amount": position_values["liability_amount"],
                "equity_amount": position_values["equity_amount"],
                "unclosed_net_income_amount": position_values[
                    "unclosed_net_income_amount"
                ],
                "expected_asset_amount": position_expected,
                "difference_amount": (
                    position_values["asset_amount"] - position_expected
                ),
            },
            [
                "balance_sheet.statement_lines",
                "balance_sheet.net_income_amount",
            ],
        ),
        _control_record(
            "changes_in_equity.rollforward",
            {
                **equity_values,
                "expected_closing_equity_amount": equity_expected,
                "difference_amount": (
                    equity_values["closing_equity_amount"] - equity_expected
                ),
            },
            ["changes_in_equity.statement_lines"],
        ),
        _control_record(
            "cash_flow.rollforward",
            {
                "opening_cash_amount": cash_values["opening_cash_amount"],
                "net_cash_change_amount": cash_values[
                    "net_cash_change_amount"
                ],
                "closing_cash_amount": cash_values["closing_cash_amount"],
                "expected_closing_cash_amount": cash_expected,
                "difference_amount": (
                    cash_values["closing_cash_amount"] - cash_expected
                ),
            },
            ["cash_flow.statement_lines"],
        ),
    ]
    if comparison_report:
        comparison_profit = comparison_report["profit_loss"]
        change_amount = (
            profit_values["net_income_amount"]
            - comparison_profit["net_income_amount"]
        )
        explanation_records.insert(
            1,
            {
                "explanation_code": "profit_loss.net_income_change",
                "status_code": "informational",
                "direction_code": _direction(change_amount),
                "parameter_map": {
                    "current_net_income_amount": _amount_text(
                        profit_values["net_income_amount"]
                    ),
                    "comparison_net_income_amount": _amount_text(
                        comparison_profit["net_income_amount"]
                    ),
                    "change_amount": _amount_text(change_amount),
                },
                "source_evidence_paths": [
                    "income_statement.net_income_amount",
                    "income_statement.comparison_net_income_amount",
                ],
            },
        )
    return explanation_records


def _control_record(
    explanation_code: str,
    parameter_values: Mapping[str, Decimal],
    evidence_paths: Sequence[str],
) -> dict[str, object]:
    """Create one balanced control explanation with exact decimal parameters."""
    return {
        "explanation_code": explanation_code,
        "status_code": "balanced",
        "direction_code": "unchanged",
        "parameter_map": {
            parameter_name: _amount_text(parameter_value)
            for parameter_name, parameter_value in parameter_values.items()
        },
        "source_evidence_paths": list(evidence_paths),
    }


def _direction(change_amount: Decimal) -> str:
    """Classify an exact amount as increase, decrease, or unchanged."""
    if change_amount > 0:
        return "increase"
    if change_amount < 0:
        return "decrease"
    return "unchanged"


def _snapshot_references(
    statement_documents: Mapping[str, Mapping[str, object]],
) -> list[str]:
    """Require each claimed snapshot identity to agree across all four statements."""
    snapshot_references: list[str] = []
    for snapshot_key in (
        "snapshot_record_id",
        "comparison_snapshot_record_id",
    ):
        references = [
            _optional_text(
                statement_documents[statement_type].get(snapshot_key),
                snapshot_key,
            )
            for statement_type in _STATEMENT_TYPES
        ]
        first_reference = references[0]
        if any(reference != first_reference for reference in references[1:]):
            raise AccountingValidationError("snapshot references do not match")
        if first_reference:
            snapshot_references.append(first_reference)
    return snapshot_references
