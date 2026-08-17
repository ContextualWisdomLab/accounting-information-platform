"""Accept a Billing journal proposal and return an AIS posting receipt."""

from __future__ import annotations

from datetime import date
from typing import Mapping

from .core import AccountingValidationError, PeriodCloseReceipt
from .ingest import ingest_journal_proposal
from .persistence import PostgresPostingLedger, _format_timestamp

_FISCAL_PERIOD_PREFIX = "urn:cwl:accounting:fiscal_period:"


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
