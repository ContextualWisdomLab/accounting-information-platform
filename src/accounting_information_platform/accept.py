"""Accept a Billing journal proposal and return an AIS posting receipt."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping
from uuid import UUID

from .core import (
    AccountingValidationError,
    PeriodCloseReceipt,
    PostedJournalLine,
    _parse_amount,
    _require_currency,
)
from .ingest import ingest_journal_proposal
from .persistence import PostgresPostingLedger, _format_timestamp

_FISCAL_PERIOD_PREFIX = "urn:cwl:accounting:fiscal_period:"
_JOURNAL_LIST_DEFAULT_PAGE_LIMIT = 50
_JOURNAL_LIST_MAX_PAGE_LIMIT = 100
_ALLOWED_OUTBOX_EVENT_TYPE_CODES = frozenset(
    {"posting_receipt", "period_close", "journal_reversal"}
)


def accept_journal_proposal(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Ingest *payload* and post it for *tenant_reference* through catalog policy."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "journal proposal payload must be a JSON object. "
            "Supply a Billing accounting_journal_proposal, then retry accept."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "proposal tenant_reference does not match the bound tenant. "
            "Call accept_journal_proposal with that tenant_reference, then retry."
        )
    proposal = ingest_journal_proposal(payload)
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    ledger.post_proposal(proposal)
    return ledger.load_published_receipt(proposal)


def accept_adjusting_journal(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Post one AIS-owned adjusting journal for *tenant_reference* and return the receipt."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "adjusting journal payload must be a JSON object. "
            "Supply an AIS adjusting journal, then retry the journal post."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "adjusting journal tenant_reference does not match the bound tenant. "
            "Call accept_adjusting_journal with that tenant_reference, then retry."
        )
    legal_entity_reference = str(payload.get("legal_entity_reference") or "")
    accounting_book_reference = str(payload.get("accounting_book_reference") or "")
    period_code = _period_code_from_reference(
        str(payload.get("fiscal_period_reference") or payload.get("period_code") or "")
    )
    idempotency_key = str(payload.get("idempotency_key") or "")
    journal_description = str(payload.get("journal_description") or "")
    if not legal_entity_reference or not accounting_book_reference or not period_code:
        raise AccountingValidationError(
            "legal_entity_reference, accounting_book_reference, and fiscal_period_reference are required. "
            "Supply those adjusting-journal fields, then retry the journal post."
        )
    if not idempotency_key:
        raise AccountingValidationError(
            "idempotency_key is required. "
            "Supply the adjusting-journal idempotency key, then retry the journal post."
        )
    if not journal_description:
        raise AccountingValidationError(
            "journal_description is required. "
            "Supply the adjusting-journal description, then retry the journal post."
        )
    journal_date = _parse_journal_date(str(payload.get("journal_date") or ""))
    lines, transaction_currency = _parse_adjusting_journal_lines(payload.get("journal_lines"))
    source_payload_hash = _adjusting_journal_hash(
        tenant_reference=tenant_reference,
        legal_entity_reference=legal_entity_reference,
        accounting_book_reference=accounting_book_reference,
        period_code=period_code,
        journal_date=journal_date,
        idempotency_key=idempotency_key,
        journal_description=journal_description,
        lines=lines,
        transaction_currency=transaction_currency,
    )
    proposal_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_reference}:{idempotency_key}")
    )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    ledger.post_adjusting_journal(
        legal_entity_reference=legal_entity_reference,
        accounting_book_reference=accounting_book_reference,
        period_code=period_code,
        journal_date=journal_date,
        idempotency_key=idempotency_key,
        source_payload_hash=source_payload_hash,
        proposal_id=proposal_id,
        transaction_currency=transaction_currency,
        lines=lines,
    )
    return ledger.load_published_receipt_by_key(idempotency_key)


def lookup_published_receipt(
    database_url: str, tenant_reference: str, idempotency_key: str
) -> dict[str, object]:
    """Return the persisted posting receipt for *tenant_reference* and *idempotency_key*."""
    if not idempotency_key:
        raise AccountingValidationError(
            "idempotency key is required. "
            "Supply the Billing idempotency key, then retry the receipt read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_published_receipt_by_key(idempotency_key)


def accept_journal_reversal(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Reverse one posted journal for *tenant_reference* and return the reversing receipt."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "journal reversal payload must be a JSON object. "
            "Supply a journal-reversal command, then retry the reverse."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "reversal tenant_reference does not match the bound tenant. "
            "Call accept_journal_reversal with that tenant_reference, then retry."
        )
    journal_reference = str(payload.get("journal_reference") or "")
    idempotency_key = str(payload.get("idempotency_key") or "")
    reversal_reason_code = str(payload.get("reversal_reason_code") or "")
    if not journal_reference and not idempotency_key:
        raise AccountingValidationError(
            "journal_reference or idempotency_key is required. "
            "Supply the posted journal or the Billing idempotency key, then retry the reverse."
        )
    if not reversal_reason_code:
        raise AccountingValidationError(
            "reversal_reason_code is required. "
            "Supply a reversal reason code, then retry the reverse."
        )
    reversal_date = _parse_reversal_date(str(payload.get("reversal_date") or ""))
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    if idempotency_key:
        original = lookup_published_receipt(database_url, tenant_reference, idempotency_key)
        resolved_reference = str(original["journal_reference"])
        if journal_reference and journal_reference != resolved_reference:
            raise AccountingValidationError(
                "journal_reference and idempotency_key do not match the same posted journal. "
                "Supply one identity, then retry the reverse."
            )
        journal_reference = resolved_reference
    policy = ledger.load_reversal_policy(journal_reference, reversal_date)
    ledger.reverse(journal_reference, reversal_date, reversal_reason_code, policy)
    return ledger.load_published_receipt_by_key(f"reversal:{journal_reference}")


def accept_period_close(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Close one fiscal period for *tenant_reference*; omit period_status_code to hard-close."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "period close payload must be a JSON object. "
            "Supply a period-close command, then retry the close."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "close tenant_reference does not match the bound tenant. "
            "Call accept_period_close with that tenant_reference, then retry."
        )
    legal_entity_reference = str(payload.get("legal_entity_reference") or "")
    book_reference = str(
        payload.get("book_reference") or payload.get("accounting_book_reference") or ""
    )
    period_code = _period_code_from_reference(
        str(payload.get("fiscal_period_reference") or payload.get("period_code") or "")
    )
    snapshot_currency_code = str(payload.get("snapshot_currency_code") or "")
    if not legal_entity_reference or not book_reference or not period_code:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those close command fields, then retry the close."
        )
    if not snapshot_currency_code:
        raise AccountingValidationError(
            "snapshot_currency_code is required. "
            "Supply the book reporting currency, then retry the close."
        )
    period_status_code = str(payload.get("period_status_code") or "hard_closed")
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    receipt = ledger.close_fiscal_period(
        legal_entity_reference=legal_entity_reference,
        accounting_book_reference=book_reference,
        period_code=period_code,
        snapshot_currency_code=snapshot_currency_code,
        period_status_code=period_status_code,
    )
    return _period_close_document(receipt)


def accept_period_open(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Open one fiscal period for *tenant_reference* and return the open receipt."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "period open payload must be a JSON object. "
            "Supply a period-open command, then retry the period open."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "open tenant_reference does not match the bound tenant. "
            "Call accept_period_open with that tenant_reference, then retry."
        )
    legal_entity_reference = str(payload.get("legal_entity_reference") or "")
    period_code = _period_code_from_reference(
        str(payload.get("fiscal_period_reference") or payload.get("period_code") or "")
    )
    if not legal_entity_reference or not period_code:
        raise AccountingValidationError(
            "legal_entity_reference and fiscal_period_reference are required. "
            "Supply those period-open fields, then retry the period open."
        )
    start_text = str(payload.get("period_start_date") or "")
    end_text = str(payload.get("period_end_date") or "")
    period_start_date = (
        _parse_period_date(start_text, "period_start_date") if start_text else None
    )
    period_end_date = _parse_period_date(end_text, "period_end_date") if end_text else None
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.open_fiscal_period(
        legal_entity_reference,
        period_code,
        period_start_date,
        period_end_date,
    )


def lookup_fiscal_period(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    fiscal_period_reference: str,
) -> dict[str, object]:
    """Return the persisted fiscal-period status and dates for one tenant entity."""
    if not legal_entity_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference and fiscal_period_reference are required. "
            "Supply those period fields, then retry the period read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_fiscal_period(
        legal_entity_reference,
        _period_code_from_reference(fiscal_period_reference),
    )


def lookup_fiscal_periods(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return one page of existing fiscal periods for a tenant legal entity."""
    if not legal_entity_reference:
        raise AccountingValidationError(
            "legal_entity_reference is required. "
            "Supply that period-list field, then retry the period list."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_fiscal_periods(
        legal_entity_reference,
        page_limit=_resolve_fiscal_period_list_page_limit(page_limit),
        cursor_after=_parse_fiscal_period_list_cursor(cursor),
    )


def lookup_account_rollforward(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
    fiscal_period_reference: str,
    chart_account_code: str,
    statement_scope_code: str = "",
) -> dict[str, object]:
    """Return opening, period, and closing sides for one tenant book, period, and chart account."""
    if not legal_entity_reference or not book_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those account-rollforward fields, then retry the account-rollforward read."
        )
    if not chart_account_code:
        raise AccountingValidationError(
            "chart_account_code is required. "
            "Supply that account-rollforward field, then retry the account-rollforward read."
        )
    if statement_scope_code and statement_scope_code not in {"period", "year_to_date"}:
        raise AccountingValidationError(
            "statement_scope_code must be period or year_to_date. "
            "Supply a known statement scope, then retry the account-rollforward read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_account_rollforward(
        legal_entity_reference,
        book_reference,
        _period_code_from_reference(fiscal_period_reference),
        chart_account_code,
        statement_scope_code=statement_scope_code,
    )


def lookup_account_balances(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
    fiscal_period_reference: str,
    chart_account_code: str = "",
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return as-of chart-account balances for one tenant book and fiscal period."""
    if not legal_entity_reference or not book_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those account-balance fields, then retry the account-balance read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_account_balances(
        legal_entity_reference,
        book_reference,
        _period_code_from_reference(fiscal_period_reference),
        chart_account_code,
        page_limit=_resolve_account_balance_page_limit(page_limit),
        cursor=cursor,
    )


def lookup_account_ledger(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    chart_account_code: str,
    fiscal_period_reference: str = "",
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return posted journal lines for one tenant entity and statutory chart account."""
    if not legal_entity_reference:
        raise AccountingValidationError(
            "legal_entity_reference is required. "
            "Supply that ledger field, then retry the account-ledger read."
        )
    if not chart_account_code:
        raise AccountingValidationError(
            "chart_account_code is required. "
            "Supply that ledger field, then retry the account-ledger read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_account_ledger(
        legal_entity_reference,
        chart_account_code,
        fiscal_period_reference,
        page_limit=_resolve_account_ledger_page_limit(page_limit),
        cursor_after=_parse_account_ledger_cursor(cursor),
    )


def lookup_period_journals(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
    fiscal_period_reference: str,
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return one page of existing posted and reversing journals for a tenant period."""
    if not legal_entity_reference or not book_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those journal-list fields, then retry the journal list."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_period_journals(
        legal_entity_reference,
        book_reference,
        _period_code_from_reference(fiscal_period_reference),
        page_limit=_resolve_journal_list_page_limit(page_limit),
        cursor_after=_parse_journal_list_cursor(cursor),
    )


def lookup_journal_reversals(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    original_journal_reference: str = "",
    fiscal_period_reference: str = "",
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return one page of existing journal reversals for a tenant legal entity."""
    if not legal_entity_reference:
        raise AccountingValidationError(
            "legal_entity_reference is required. "
            "Supply that journal-reversal list field, then retry the journal-reversal list."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_journal_reversals(
        legal_entity_reference,
        original_journal_reference,
        _period_code_from_reference(fiscal_period_reference),
        page_limit=_resolve_journal_reversal_page_limit(page_limit),
        cursor_after=_parse_journal_reversal_cursor(cursor),
    )


def lookup_period_closes(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    fiscal_period_reference: str = "",
    period_status_code: str = "",
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return one page of durable hard-close receipts for a tenant legal entity."""
    if not legal_entity_reference:
        raise AccountingValidationError(
            "legal_entity_reference is required. "
            "Supply that period-close list field, then retry the period-close list."
        )
    if period_status_code and period_status_code not in {"soft_closed", "hard_closed"}:
        raise AccountingValidationError(
            "period_status_code must be soft_closed or hard_closed. "
            "Supply a known period_status_code, then retry the period-close list."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_period_closes(
        legal_entity_reference,
        _period_code_from_reference(fiscal_period_reference),
        period_status_code,
        page_limit=_resolve_period_close_page_limit(page_limit),
        cursor_after=_parse_period_close_cursor(cursor),
    )


def lookup_outbox_events(
    database_url: str,
    tenant_reference: str,
    event_type_code: str,
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return unpublished outbox rows for one tenant and event type."""
    if event_type_code not in _ALLOWED_OUTBOX_EVENT_TYPE_CODES:
        raise AccountingValidationError(
            "event_type_code must be posting_receipt, period_close, or journal_reversal. "
            "Supply a supported outbox event_type_code, then retry the outbox read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_unpublished_outbox_events(
        event_type_code,
        page_limit=_resolve_outbox_page_limit(page_limit),
        cursor_after=_parse_outbox_cursor(cursor),
    )


def lookup_audit_events(
    database_url: str,
    tenant_reference: str,
    event_type_code: str = "",
    page_limit: int | None = None,
    cursor: str = "",
) -> dict[str, object]:
    """Return published and unpublished outbox rows for one tenant without marking publish."""
    if event_type_code and event_type_code not in _ALLOWED_OUTBOX_EVENT_TYPE_CODES:
        raise AccountingValidationError(
            "event_type_code must be posting_receipt, period_close, or journal_reversal. "
            "Supply a supported audit event_type_code, then retry the audit-event read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_audit_events(
        event_type_code,
        page_limit=_resolve_audit_event_page_limit(page_limit),
        cursor_after=_parse_outbox_cursor(cursor),
    )


def publish_outbox_event(
    database_url: str,
    tenant_reference: str,
    outbox_event_id: str,
) -> dict[str, object]:
    """Mark one tenant-owned outbox row published without rewriting other facts."""
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.publish_outbox_event(outbox_event_id)


def lookup_posted_journal(
    database_url: str,
    tenant_reference: str,
    idempotency_key: str = "",
    journal_reference: str = "",
) -> dict[str, object]:
    """Return the persisted journal and lines for one tenant key or journal reference."""
    if not idempotency_key and not journal_reference:
        raise AccountingValidationError(
            "idempotency_key or journal_reference is required. "
            "Supply the Billing key or the posted journal reference, then retry the journal read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_posted_journal(idempotency_key, journal_reference)


def lookup_account_role_mappings(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
) -> dict[str, object]:
    """Return effective catalog mappings for one tenant, legal entity, and book."""
    if not legal_entity_reference or not book_reference:
        raise AccountingValidationError(
            "legal_entity_reference and book_reference are required. "
            "Supply those catalog fields, then retry the mapping read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_account_role_mappings(legal_entity_reference, book_reference)


def lookup_legal_entities(
    database_url: str,
    tenant_reference: str,
) -> dict[str, object]:
    """Return existing legal entities for the bound tenant."""
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_legal_entities()


def lookup_accounting_books(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
) -> dict[str, object]:
    """Return existing accounting books for one tenant legal entity."""
    if not legal_entity_reference:
        raise AccountingValidationError(
            "legal_entity_reference is required. "
            "Supply that catalog field, then retry the accounting-book list."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_accounting_books(legal_entity_reference)


def lookup_chart_accounts(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
) -> dict[str, object]:
    """Return existing chart accounts for one tenant, legal entity, and book."""
    if not legal_entity_reference or not book_reference:
        raise AccountingValidationError(
            "legal_entity_reference and book_reference are required. "
            "Supply those catalog fields, then retry the chart-account read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_chart_accounts(legal_entity_reference, book_reference)


def lookup_trial_balance(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
    fiscal_period_reference: str,
    balance_basis_code: str = "",
) -> dict[str, object]:
    """Return the snapshot or live trial balance, optionally on an unadjusted, adjusted, or post-close basis."""
    if not legal_entity_reference or not book_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those trial-balance fields, then retry the trial-balance read."
        )
    if balance_basis_code and balance_basis_code not in {
        "unadjusted",
        "adjusted",
        "post_close",
    }:
        raise AccountingValidationError(
            "balance_basis_code must be unadjusted, adjusted, or post_close. "
            "Supply a known trial-balance basis, then retry the trial-balance read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_period_trial_balance(
        legal_entity_reference=legal_entity_reference,
        accounting_book_reference=book_reference,
        period_code=_period_code_from_reference(fiscal_period_reference),
        balance_basis_code=balance_basis_code,
    )


def lookup_financial_statement(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
    fiscal_period_reference: str,
    statement_type_code: str,
    comparison_fiscal_period_reference: str = "",
    statement_scope_code: str = "",
) -> dict[str, object]:
    """Return the income statement, balance sheet, changes in equity, or cash flow for one book and period."""
    if not legal_entity_reference or not book_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those financial-statement fields, then retry the financial-statement read."
        )
    if statement_type_code not in {
        "income_statement",
        "balance_sheet",
        "changes_in_equity",
        "cash_flow",
    }:
        raise AccountingValidationError(
            "statement_type_code must be income_statement, balance_sheet, changes_in_equity, or cash_flow. "
            "Supply a known statement type, then retry the financial-statement read."
        )
    if statement_scope_code and statement_scope_code not in {"period", "year_to_date"}:
        raise AccountingValidationError(
            "statement_scope_code must be period or year_to_date. "
            "Supply a known statement scope, then retry the financial-statement read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_financial_statement(
        legal_entity_reference,
        book_reference,
        _period_code_from_reference(fiscal_period_reference),
        statement_type_code,
        comparison_period_code=_period_code_from_reference(
            comparison_fiscal_period_reference
        ),
        statement_scope_code=statement_scope_code,
    )


def _parse_period_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AccountingValidationError(
            f"{field_name} must be an ISO-8601 date. "
            f"Supply {field_name}, then retry the period open."
        ) from error


def _parse_journal_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AccountingValidationError(
            "journal_date must be an ISO-8601 date. "
            "Supply journal_date, then retry the journal post."
        ) from error


def _parse_adjusting_journal_lines(
    raw_lines: object,
) -> tuple[tuple[PostedJournalLine, ...], str]:
    if not isinstance(raw_lines, list) or len(raw_lines) < 2:
        if not isinstance(raw_lines, list):
            raise AccountingValidationError(
                "journal_lines must be an array. "
                "Supply balanced adjusting journal_lines, then retry the journal post."
            )
        raise AccountingValidationError(
            "adjusting journal requires at least two lines. "
            "Supply balanced adjusting journal_lines, then retry the journal post."
        )
    parsed: list[PostedJournalLine] = []
    transaction_currency = ""
    debit_total = Decimal("0")
    credit_total = Decimal("0")
    for index, raw_line in enumerate(raw_lines, start=1):
        if not isinstance(raw_line, Mapping):
            raise AccountingValidationError(
                "each journal line must be a JSON object. "
                "Supply chart_account_code, debit_credit_code, amount, and currency_code, "
                "then retry the journal post."
            )
        chart_account_code = str(raw_line.get("chart_account_code") or "")
        debit_credit_code = str(raw_line.get("debit_credit_code") or "")
        currency_code = str(raw_line.get("currency_code") or "")
        if not chart_account_code:
            raise AccountingValidationError(
                "chart_account_code is required. "
                "Supply a statutory chart_account_code, then retry the journal post."
            )
        if debit_credit_code not in {"debit", "credit"}:
            raise AccountingValidationError(
                "debit_credit_code must be debit or credit. "
                "Supply debit_credit_code, then retry the journal post."
            )
        _require_currency(currency_code)
        if transaction_currency and currency_code != transaction_currency:
            raise AccountingValidationError(
                "journal_lines currency_code must match on every line. "
                "Supply one book currency, then retry the journal post."
            )
        transaction_currency = currency_code
        amount = _parse_amount(str(raw_line.get("amount") or ""))
        debit_amount = amount if debit_credit_code == "debit" else Decimal("0")
        credit_amount = amount if debit_credit_code == "credit" else Decimal("0")
        debit_total += debit_amount
        credit_total += credit_amount
        parsed.append(
            PostedJournalLine(
                line_number=index,
                chart_account_code=chart_account_code,
                account_role_code="adjusting",
                debit_amount=debit_amount,
                credit_amount=credit_amount,
            )
        )
    if debit_total != credit_total:
        raise AccountingValidationError(
            "adjusting journal must balance. "
            "Correct the journal_lines amounts, then retry the journal post."
        )
    return tuple(parsed), transaction_currency


def _adjusting_journal_hash(
    *,
    tenant_reference: str,
    legal_entity_reference: str,
    accounting_book_reference: str,
    period_code: str,
    journal_date: date,
    idempotency_key: str,
    journal_description: str,
    lines: tuple[PostedJournalLine, ...],
    transaction_currency: str,
) -> str:
    payload = {
        "accounting_book_reference": accounting_book_reference,
        "idempotency_key": idempotency_key,
        "journal_date": journal_date.isoformat(),
        "journal_description": journal_description,
        "journal_lines": [
            {
                "chart_account_code": line.chart_account_code,
                "credit_amount": format(line.credit_amount, "f"),
                "currency_code": transaction_currency,
                "debit_amount": format(line.debit_amount, "f"),
            }
            for line in lines
        ],
        "legal_entity_reference": legal_entity_reference,
        "period_code": period_code,
        "tenant_reference": tenant_reference,
    }
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _parse_reversal_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AccountingValidationError(
            "reversal_date must be an ISO-8601 date. "
            "Supply reversal_date, then retry the reverse."
        ) from error


def _resolve_journal_list_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply a journal-list page_limit, then retry the journal list.",
    )


def _resolve_journal_reversal_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply a journal-reversal page_limit, then retry the journal-reversal list.",
    )


def _resolve_period_close_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply a period-close page_limit, then retry the period-close list.",
    )


def _resolve_outbox_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply an outbox-event page_limit, then retry the outbox read.",
    )


def _resolve_audit_event_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply an audit-event page_limit, then retry the audit-event read.",
    )


def _resolve_fiscal_period_list_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply a fiscal-period-list page_limit, then retry the period list.",
    )


def _resolve_account_balance_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply an account-balance page_limit, then retry the account-balance read.",
    )


def _resolve_account_ledger_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply an account-ledger page_limit, then retry the account-ledger read.",
    )


def _resolve_bounded_page_limit(page_limit: int | None, next_action: str) -> int:
    if page_limit is None:
        return _JOURNAL_LIST_DEFAULT_PAGE_LIMIT
    if page_limit < 1 or page_limit > _JOURNAL_LIST_MAX_PAGE_LIMIT:
        raise AccountingValidationError(
            f"page_limit must be between 1 and 100. {next_action}"
        )
    return page_limit


def _parse_journal_list_cursor(cursor: str) -> tuple[date, str] | None:
    if not cursor:
        return None
    if "|" not in cursor:
        raise AccountingValidationError(
            "cursor must be accounting_date|journal_reference. "
            "Supply a journal-list cursor, then retry the journal list."
        )
    date_text, journal_reference = cursor.split("|", 1)
    if not date_text or not journal_reference:
        raise AccountingValidationError(
            "cursor must be accounting_date|journal_reference. "
            "Supply a journal-list cursor, then retry the journal list."
        )
    try:
        accounting_date = date.fromisoformat(date_text)
    except ValueError as error:
        raise AccountingValidationError(
            "cursor must be accounting_date|journal_reference. "
            "Supply a journal-list cursor, then retry the journal list."
        ) from error
    return accounting_date, journal_reference


def _parse_journal_reversal_cursor(cursor: str) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    if "|" not in cursor:
        raise AccountingValidationError(
            "cursor must be posted_at|journal_reference. "
            "Supply a journal-reversal cursor, then retry the journal-reversal list."
        )
    posted_at_text, journal_reference = cursor.split("|", 1)
    if not posted_at_text or not journal_reference:
        raise AccountingValidationError(
            "cursor must be posted_at|journal_reference. "
            "Supply a journal-reversal cursor, then retry the journal-reversal list."
        )
    try:
        posted_at = datetime.fromisoformat(posted_at_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingValidationError(
            "cursor must be posted_at|journal_reference. "
            "Supply a journal-reversal cursor, then retry the journal-reversal list."
        ) from error
    return posted_at, journal_reference


def _parse_period_close_cursor(cursor: str) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    if "|" not in cursor:
        raise AccountingValidationError(
            "cursor must be snapshot_generated_at|snapshot_record_id. "
            "Supply a period-close cursor, then retry the period-close list."
        )
    generated_at_text, snapshot_record_id_text = cursor.split("|", 1)
    if not generated_at_text or not snapshot_record_id_text:
        raise AccountingValidationError(
            "cursor must be snapshot_generated_at|snapshot_record_id. "
            "Supply a period-close cursor, then retry the period-close list."
        )
    try:
        generated_at = datetime.fromisoformat(generated_at_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingValidationError(
            "cursor must be snapshot_generated_at|snapshot_record_id. "
            "Supply a period-close cursor, then retry the period-close list."
        ) from error
    try:
        return generated_at, UUID(snapshot_record_id_text)
    except ValueError as error:
        raise AccountingValidationError(
            "cursor snapshot_record_id must be a UUID. "
            "Supply a period-close cursor, then retry the period-close list."
        ) from error


def _parse_fiscal_period_list_cursor(cursor: str) -> tuple[date, str] | None:
    if not cursor:
        return None
    if "|" not in cursor:
        raise AccountingValidationError(
            "cursor must be period_start_date|period_code. "
            "Supply a fiscal-period-list cursor, then retry the period list."
        )
    date_text, period_code = cursor.split("|", 1)
    if not date_text or not period_code:
        raise AccountingValidationError(
            "cursor must be period_start_date|period_code. "
            "Supply a fiscal-period-list cursor, then retry the period list."
        )
    try:
        period_start_date = date.fromisoformat(date_text)
    except ValueError as error:
        raise AccountingValidationError(
            "cursor must be period_start_date|period_code. "
            "Supply a fiscal-period-list cursor, then retry the period list."
        ) from error
    return period_start_date, period_code


def _parse_account_ledger_cursor(cursor: str) -> tuple[datetime, str, int] | None:
    if not cursor:
        return None
    parts = cursor.split("|")
    if len(parts) != 3:
        raise AccountingValidationError(
            "cursor must be posted_at|journal_reference|line_number. "
            "Supply an account-ledger cursor, then retry the account-ledger read."
        )
    posted_at_text, journal_reference, line_number_text = parts
    if not posted_at_text or not journal_reference or not line_number_text:
        raise AccountingValidationError(
            "cursor must be posted_at|journal_reference|line_number. "
            "Supply an account-ledger cursor, then retry the account-ledger read."
        )
    try:
        posted_at = datetime.fromisoformat(posted_at_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingValidationError(
            "cursor posted_at must be an ISO-8601 timestamp. "
            "Supply an account-ledger cursor, then retry the account-ledger read."
        ) from error
    try:
        return posted_at, journal_reference, int(line_number_text)
    except ValueError as error:
        raise AccountingValidationError(
            "cursor line_number must be an integer. "
            "Supply an account-ledger cursor, then retry the account-ledger read."
        ) from error


def _parse_outbox_cursor(cursor: str) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    if "|" not in cursor:
        raise AccountingValidationError(
            "cursor must be created_at|outbox_event_id. "
            "Supply an outbox-event cursor, then retry the outbox read."
        )
    created_at_text, outbox_event_id_text = cursor.split("|", 1)
    if not created_at_text or not outbox_event_id_text:
        raise AccountingValidationError(
            "cursor must be created_at|outbox_event_id. "
            "Supply an outbox-event cursor, then retry the outbox read."
        )
    try:
        created_at = datetime.fromisoformat(created_at_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingValidationError(
            "cursor created_at must be an ISO-8601 timestamp. "
            "Supply an outbox-event cursor, then retry the outbox read."
        ) from error
    try:
        return created_at, UUID(outbox_event_id_text)
    except ValueError as error:
        raise AccountingValidationError(
            "cursor outbox_event_id must be a UUID. "
            "Supply an outbox-event cursor, then retry the outbox read."
        ) from error


def _period_code_from_reference(value: str) -> str:
    if value.startswith(_FISCAL_PERIOD_PREFIX):
        return value[len(_FISCAL_PERIOD_PREFIX) :]
    return value


def _period_close_document(receipt: PeriodCloseReceipt) -> dict[str, object]:
    return {
        "tenant_reference": receipt.tenant_reference,
        "legal_entity_reference": receipt.legal_entity_reference,
        "accounting_book_reference": receipt.accounting_book_reference,
        "period_code": receipt.period_code,
        "period_status_code": receipt.period_status_code,
        "snapshot_record_id": receipt.snapshot_record_id,
        "snapshot_generated_at": _format_timestamp(receipt.snapshot_generated_at),
        "source_journal_count": receipt.source_journal_count,
        "source_payload_hash": receipt.source_payload_hash,
        "replayed": receipt.replayed,
    }
