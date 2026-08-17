"""Accept a Billing journal proposal and return an AIS posting receipt."""

from __future__ import annotations

from typing import Mapping

from .core import AccountingValidationError
from .ingest import ingest_journal_proposal
from .persistence import PostgresPostingLedger


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
