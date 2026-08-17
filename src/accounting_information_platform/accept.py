"""Accept a Billing journal proposal and return an AIS posting receipt."""

from __future__ import annotations

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
