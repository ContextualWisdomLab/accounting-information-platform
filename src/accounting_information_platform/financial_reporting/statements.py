"""Four-statement normalization, controls, and canonical fact projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..core import AccountingValidationError
from .primitives import (
    _CODE_PATTERN,
    _amount,
    _amount_text,
    _book_reference,
    _mapping_text,
    _optional_text,
)

_STATEMENT_TYPES = (
    "income_statement",
    "balance_sheet",
    "changes_in_equity",
    "cash_flow",
)


@dataclass(frozen=True, slots=True)
class _StatementLine:
    """Normalized statement line with exact amounts and an evidence locator."""

    account_role_code: str
    account_class_code: str
    debit_amount: Decimal
    credit_amount: Decimal
    evidence_path: str


def _statements(
    source_document: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    """Validate and return the four statement documents under one package identity."""
    package_identity = (
        _mapping_text(source_document, "tenant_reference"),
        _mapping_text(source_document, "legal_entity_reference"),
        _book_reference(source_document),
        _mapping_text(source_document, "fiscal_period_reference"),
        _optional_text(source_document.get("statement_scope_code"), "statement_scope_code")
        or "period",
        _optional_text(
            source_document.get("comparison_fiscal_period_reference"),
            "comparison_fiscal_period_reference",
        ),
    )
    statement_documents: dict[str, Mapping[str, object]] = {}
    for statement_type in _STATEMENT_TYPES:
        statement_document = source_document.get(statement_type)
        if not isinstance(statement_document, Mapping):
            raise AccountingValidationError(
                f"required financial statement {statement_type} is missing"
            )
        if statement_document.get("statement_type_code") != statement_type:
            raise AccountingValidationError(
                "financial statement type does not match package key"
            )
        statement_identity = (
            _mapping_text(statement_document, "tenant_reference"),
            _mapping_text(statement_document, "legal_entity_reference"),
            _book_reference(statement_document),
            _mapping_text(statement_document, "fiscal_period_reference"),
            _optional_text(
                statement_document.get("statement_scope_code"),
                "statement_scope_code",
            )
            or "period",
            _optional_text(
                statement_document.get("comparison_fiscal_period_reference"),
                "comparison_fiscal_period_reference",
            ),
        )
        if statement_identity != package_identity:
            raise AccountingValidationError(
                "financial statement identity does not match package"
            )
        statement_documents[statement_type] = statement_document
    return statement_documents


def _period_report(
    statement_documents: Mapping[str, Mapping[str, object]],
    period_code: str,
    comparison_period: bool,
) -> dict[str, object]:
    """Validate one period projection and return its facts and control values."""
    income_lines, income_net = _period_values(
        statement_documents["income_statement"],
        "income_statement",
        comparison_period,
        frozenset({"revenue", "expense"}),
    )
    position_lines, position_net = _period_values(
        statement_documents["balance_sheet"],
        "balance_sheet",
        comparison_period,
        frozenset({"asset", "liability", "equity"}),
    )
    equity_lines, _equity_net = _period_values(
        statement_documents["changes_in_equity"],
        "changes_in_equity",
        comparison_period,
        frozenset({"equity"}),
    )
    cash_lines, _cash_net = _period_values(
        statement_documents["cash_flow"],
        "cash_flow",
        comparison_period,
        frozenset({""}),
    )

    revenue_amount = _class_total(income_lines, "revenue", credit_normal=True)
    expense_amount = _class_total(income_lines, "expense", credit_normal=False)
    if revenue_amount - expense_amount != income_net:
        raise AccountingValidationError(
            "income statement does not reproduce net_income_amount"
        )
    asset_amount = _class_total(position_lines, "asset", credit_normal=False)
    liability_amount = _class_total(position_lines, "liability", credit_normal=True)
    equity_amount = _class_total(position_lines, "equity", credit_normal=True)
    if asset_amount != liability_amount + equity_amount + position_net:
        raise AccountingValidationError(
            "statement of financial position does not balance"
        )

    equity_roles = _unique_roles(
        equity_lines,
        frozenset(
            {
                "opening_equity",
                "period_net_income",
                "other_equity_movements",
                "closing_equity",
            }
        ),
    )
    if equity_roles["closing_equity"] != (
        equity_roles["opening_equity"]
        + equity_roles["period_net_income"]
        + equity_roles["other_equity_movements"]
    ):
        raise AccountingValidationError(
            "changes in equity rollforward does not balance"
        )
    if equity_roles["period_net_income"] != income_net:
        raise AccountingValidationError(
            "changes in equity period net income does not match income statement"
        )
    if equity_roles["closing_equity"] != equity_amount + position_net:
        raise AccountingValidationError(
            "changes in equity closing amount does not match financial position"
        )

    cash_roles = _unique_roles(
        cash_lines,
        frozenset(
            {
                "period_net_income",
                "operating_working_capital",
                "cash_from_operations",
                "cash_from_investing",
                "cash_from_financing",
                "net_cash_change",
                "opening_cash",
                "closing_cash",
            }
        ),
    )
    if cash_roles["cash_from_operations"] != (
        cash_roles["period_net_income"]
        + cash_roles["operating_working_capital"]
    ):
        raise AccountingValidationError(
            "cash from operations does not match net income and working capital"
        )
    if cash_roles["net_cash_change"] != (
        cash_roles["cash_from_operations"]
        + cash_roles["cash_from_investing"]
        + cash_roles["cash_from_financing"]
    ):
        raise AccountingValidationError(
            "cash flow net change does not match activity subtotals"
        )
    if cash_roles["closing_cash"] != (
        cash_roles["opening_cash"] + cash_roles["net_cash_change"]
    ):
        raise AccountingValidationError("cash flow rollforward does not balance")
    if cash_roles["period_net_income"] != income_net:
        raise AccountingValidationError(
            "cash flow period net income does not match income statement"
        )
    position_roles = _role_totals(
        position_lines,
        credit_normal_classes={"liability", "equity"},
    )
    if (
        "cash_receipt" in position_roles
        and cash_roles["closing_cash"] != position_roles["cash_receipt"]
    ):
        raise AccountingValidationError(
            "cash flow closing cash does not match financial position"
        )

    fact_records = [
        _fact(
            "profit_loss.revenue_amount",
            revenue_amount,
            period_code,
            "income_statement",
            "duration",
            _paths(income_lines, "revenue"),
        ),
        _fact(
            "profit_loss.expense_amount",
            expense_amount,
            period_code,
            "income_statement",
            "duration",
            _paths(income_lines, "expense"),
        ),
        _fact(
            "profit_loss.net_income_amount",
            income_net,
            period_code,
            "income_statement",
            "duration",
            [_amount_path("income_statement", comparison_period)],
        ),
        _fact(
            "financial_position.asset_amount",
            asset_amount,
            period_code,
            "balance_sheet",
            "instant",
            _paths(position_lines, "asset"),
        ),
        _fact(
            "financial_position.liability_amount",
            liability_amount,
            period_code,
            "balance_sheet",
            "instant",
            _paths(position_lines, "liability"),
        ),
        _fact(
            "financial_position.equity_amount",
            equity_amount,
            period_code,
            "balance_sheet",
            "instant",
            _paths(position_lines, "equity"),
        ),
        _fact(
            "financial_position.unclosed_net_income_amount",
            position_net,
            period_code,
            "balance_sheet",
            "instant",
            [_amount_path("balance_sheet", comparison_period)],
        ),
    ]
    fact_records.extend(
        _role_facts(
            "profit_loss.account_role",
            income_lines,
            period_code,
            "income_statement",
        )
    )
    fact_records.extend(
        _role_facts(
            "financial_position.account_role",
            position_lines,
            period_code,
            "balance_sheet",
        )
    )
    fact_records.extend(
        _named_role_facts(
            "changes_in_equity",
            equity_roles,
            equity_lines,
            period_code,
        )
    )
    fact_records.extend(
        _named_role_facts("cash_flow", cash_roles, cash_lines, period_code)
    )
    return {
        "fact_records": fact_records,
        "profit_loss": {
            "revenue_amount": revenue_amount,
            "expense_amount": expense_amount,
            "net_income_amount": income_net,
        },
        "financial_position": {
            "asset_amount": asset_amount,
            "liability_amount": liability_amount,
            "equity_amount": equity_amount,
            "unclosed_net_income_amount": position_net,
        },
        "equity": {
            "opening_equity_amount": equity_roles["opening_equity"],
            "period_net_income_amount": equity_roles["period_net_income"],
            "other_equity_movements_amount": equity_roles[
                "other_equity_movements"
            ],
            "closing_equity_amount": equity_roles["closing_equity"],
        },
        "cash_flow": {
            f"{role_code}_amount": role_amount
            for role_code, role_amount in cash_roles.items()
        },
    }


def _period_values(
    statement_document: Mapping[str, object],
    statement_type: str,
    comparison_period: bool,
    allowed_classes: frozenset[str],
) -> tuple[list[_StatementLine], Decimal]:
    """Normalize one statement period and verify its reported debit and credit totals."""
    key_prefix = "comparison_" if comparison_period else ""
    line_key = f"{key_prefix}statement_lines"
    raw_lines = statement_document.get(line_key)
    if not isinstance(raw_lines, Sequence) or isinstance(raw_lines, (str, bytes)):
        raise AccountingValidationError(f"{statement_type}.{line_key} must be a list")
    line_records: list[_StatementLine] = []
    for line_index, raw_line in enumerate(raw_lines):
        if not isinstance(raw_line, Mapping):
            raise AccountingValidationError(
                f"{statement_type}.{line_key}[{line_index}] is invalid"
            )
        role_code = _mapping_text(raw_line, "account_role_code")
        class_code = raw_line.get("account_class_code")
        chart_code = raw_line.get("chart_account_code")
        if _CODE_PATTERN.fullmatch(role_code) is None:
            raise AccountingValidationError("account_role_code is invalid")
        if not isinstance(class_code, str) or class_code not in allowed_classes:
            raise AccountingValidationError("account_class_code is invalid")
        if not isinstance(chart_code, str) or chart_code.strip() != chart_code:
            raise AccountingValidationError("chart_account_code is invalid")
        if statement_type in {"income_statement", "balance_sheet"} and not chart_code:
            raise AccountingValidationError("chart_account_code is invalid")
        debit_amount = _amount(raw_line.get("debit_amount"), "debit_amount")
        credit_amount = _amount(raw_line.get("credit_amount"), "credit_amount")
        if debit_amount < 0 or credit_amount < 0:
            raise AccountingValidationError("statement amounts must be non-negative")
        if debit_amount and credit_amount:
            raise AccountingValidationError(
                "statement line has both debit and credit amounts"
            )
        line_records.append(
            _StatementLine(
                role_code,
                class_code,
                debit_amount,
                credit_amount,
                f"{statement_type}.{line_key}[{line_index}]",
            )
        )
    debit_total = sum(
        (line_record.debit_amount for line_record in line_records),
        Decimal("0"),
    )
    credit_total = sum(
        (line_record.credit_amount for line_record in line_records),
        Decimal("0"),
    )
    if debit_total != _amount(
        statement_document.get(f"{key_prefix}total_debit_amount"),
        "total_debit_amount",
    ) or credit_total != _amount(
        statement_document.get(f"{key_prefix}total_credit_amount"),
        "total_credit_amount",
    ):
        raise AccountingValidationError(
            f"{statement_type} totals do not match statement lines"
        )
    return line_records, _amount(
        statement_document.get(f"{key_prefix}net_income_amount"),
        "net_income_amount",
    )


def _class_total(
    line_records: Sequence[_StatementLine],
    class_code: str,
    *,
    credit_normal: bool,
) -> Decimal:
    """Return a debit- or credit-normal total for one account class."""
    return sum(
        (
            (
                line_record.credit_amount - line_record.debit_amount
                if credit_normal
                else line_record.debit_amount - line_record.credit_amount
            )
            for line_record in line_records
            if line_record.account_class_code == class_code
        ),
        Decimal("0"),
    )


def _role_totals(
    line_records: Sequence[_StatementLine],
    *,
    credit_normal_classes: set[str],
) -> dict[str, Decimal]:
    """Aggregate exact statement amounts by authoritative account role."""
    role_amounts: dict[str, Decimal] = {}
    for line_record in line_records:
        line_amount = (
            line_record.credit_amount - line_record.debit_amount
            if line_record.account_class_code in credit_normal_classes
            else line_record.debit_amount - line_record.credit_amount
        )
        role_amounts[line_record.account_role_code] = (
            role_amounts.get(line_record.account_role_code, Decimal("0"))
            + line_amount
        )
    return role_amounts


def _unique_roles(
    line_records: Sequence[_StatementLine],
    required_roles: frozenset[str],
) -> dict[str, Decimal]:
    """Require each rollforward role exactly once and return its signed amount."""
    role_amounts: dict[str, Decimal] = {}
    for line_record in line_records:
        if line_record.account_role_code not in required_roles:
            raise AccountingValidationError(
                "statement contains an unexpected account_role_code"
            )
        if line_record.account_role_code in role_amounts:
            raise AccountingValidationError(
                "statement contains a duplicate account_role_code"
            )
        role_amounts[line_record.account_role_code] = (
            line_record.credit_amount - line_record.debit_amount
        )
    if required_roles.difference(role_amounts):
        raise AccountingValidationError(
            "statement is missing required account_role_code values"
        )
    return role_amounts


def _role_facts(
    fact_prefix: str,
    line_records: Sequence[_StatementLine],
    period_code: str,
    statement_type: str,
) -> list[dict[str, object]]:
    """Convert grouped account-role amounts into canonical fact records."""
    credit_normal_classes = {"revenue", "liability", "equity"}
    role_amounts = _role_totals(
        line_records,
        credit_normal_classes=credit_normal_classes,
    )
    return _named_role_facts(
        fact_prefix,
        role_amounts,
        line_records,
        period_code,
        statement_type,
    )


def _named_role_facts(
    fact_prefix: str,
    role_amounts: Mapping[str, Decimal],
    line_records: Sequence[_StatementLine],
    period_code: str,
    statement_type: str | None = None,
) -> list[dict[str, object]]:
    """Emit deterministic role facts in lexical role-code order."""
    return [
        _fact(
            f"{fact_prefix}.{role_code}_amount",
            role_amounts[role_code],
            period_code,
            statement_type or fact_prefix,
            _role_period_type(fact_prefix, role_code),
            [
                line_record.evidence_path
                for line_record in line_records
                if line_record.account_role_code == role_code
            ],
        )
        for role_code in sorted(role_amounts)
    ]


def _role_period_type(fact_prefix: str, role_code: str) -> str:
    """Return the canonical duration or instant period type for one role fact."""
    if fact_prefix == "profit_loss.account_role":
        return "duration"
    if fact_prefix == "financial_position.account_role":
        return "instant"
    if fact_prefix == "changes_in_equity":
        return (
            "instant"
            if role_code in {"opening_equity", "closing_equity"}
            else "duration"
        )
    return (
        "instant"
        if role_code in {"opening_cash", "closing_cash"}
        else "duration"
    )


def _fact(
    fact_code: str,
    fact_amount: Decimal,
    period_code: str,
    statement_type: str,
    period_type_code: str,
    evidence_paths: Sequence[str],
) -> dict[str, object]:
    """Create one exact-decimal, evidence-linked canonical fact record."""
    return {
        "fact_code": fact_code,
        "fact_amount": _amount_text(fact_amount),
        "period_context_code": period_code,
        "statement_type_code": statement_type,
        "period_type_code": period_type_code,
        "source_evidence_paths": list(evidence_paths),
    }


def _paths(
    line_records: Sequence[_StatementLine],
    class_code: str,
) -> list[str]:
    """Return evidence paths for a class, including a deterministic empty fallback."""
    evidence_paths = [
        line_record.evidence_path
        for line_record in line_records
        if line_record.account_class_code == class_code
    ]
    if evidence_paths:
        return evidence_paths
    if line_records:
        return [line_records[0].evidence_path.rsplit("[", 1)[0]]
    return [class_code]


def _amount_path(statement_type: str, comparison_period: bool) -> str:
    """Return the source path of the current or comparative net-income amount."""
    amount_key = (
        "comparison_net_income_amount"
        if comparison_period
        else "net_income_amount"
    )
    return f"{statement_type}.{amount_key}"
