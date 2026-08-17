"""Accept a Billing journal proposal and return an AIS posting receipt."""

from __future__ import annotations

from datetime import date, datetime
from typing import Mapping
from uuid import UUID

from .core import AccountingValidationError, PeriodCloseReceipt
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
    """Close one fiscal period for *tenant_reference* and return the close receipt."""
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
) -> dict[str, object]:
    """Return the snapshot or live trial balance for one book and fiscal period."""
    if not legal_entity_reference or not book_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those trial-balance fields, then retry the trial-balance read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_period_trial_balance(
        legal_entity_reference=legal_entity_reference,
        accounting_book_reference=book_reference,
        period_code=_period_code_from_reference(fiscal_period_reference),
    )


def lookup_financial_statement(
    database_url: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
    fiscal_period_reference: str,
    statement_type_code: str,
) -> dict[str, object]:
    """Return the income statement or balance sheet for one book and fiscal period."""
    if not legal_entity_reference or not book_reference or not fiscal_period_reference:
        raise AccountingValidationError(
            "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
            "Supply those financial-statement fields, then retry the financial-statement read."
        )
    if statement_type_code not in {"income_statement", "balance_sheet"}:
        raise AccountingValidationError(
            "statement_type_code must be income_statement or balance_sheet. "
            "Supply a known statement type, then retry the financial-statement read."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_financial_statement(
        legal_entity_reference,
        book_reference,
        _period_code_from_reference(fiscal_period_reference),
        statement_type_code,
    )


def _parse_period_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AccountingValidationError(
            f"{field_name} must be an ISO-8601 date. "
            f"Supply {field_name}, then retry the period open."
        ) from error


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


def _resolve_outbox_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply an outbox-event page_limit, then retry the outbox read.",
    )


def _resolve_fiscal_period_list_page_limit(page_limit: int | None) -> int:
    return _resolve_bounded_page_limit(
        page_limit,
        "Supply a fiscal-period-list page_limit, then retry the period list.",
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
