"""Ingest the Billing-owned journal proposal JSON contract.

``JournalProposal`` stays status-free. Published ``proposal_status`` is an
ingest gate: only ``validated`` and ``exported`` become posting proposals.
Operational Billing reject rows are not schema-valid proposals and are not
ingested. AIS posting status lives on ``accounting_posting_receipt``.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

from .core import AccountingValidationError, JournalLineProposal, JournalProposal


_REQUIRED_CONTRACT_FIELDS = (
    "proposal_id",
    "proposal_contract_version",
    "idempotency_key",
    "tenant_reference",
    "legal_entity_reference",
    "intended_book_role_code",
    "transaction_currency",
    "transaction_date",
    "accounting_date",
    "source_payload_hash",
    "proposed_at",
    "proposal_status",
    "source_event_references",
    "lines",
)


def ingest_journal_proposal(payload: object) -> JournalProposal:
    """Accept a Billing ``accounting_journal_proposal`` and return a posting proposal."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "journal proposal payload must be a JSON object. "
            "Supply a Billing accounting_journal_proposal, then retry ingest."
        )
    if (
        payload.get("journal_proposal_outcome_code") == "rejected"
        or "rejection_reason_code" in payload
    ):
        raise AccountingValidationError(
            "Billing operational reject rows are not schema-valid journal proposals. "
            "Correct the invoice draft or tenant named in rejection_reason_code, "
            "then retry propose_journal; do not ingest the reject row."
        )
    proposal_status = _published_proposal_status(payload)
    match proposal_status:
        case "validated" | "exported":
            return _journal_proposal_from_contract(payload)
        case "draft" | "rejected":
            raise AccountingValidationError(
                f"proposal_status {proposal_status} is not ingestible. "
                "Ask Billing to emit a validated proposal, then retry ingest."
            )
        case "posted":
            raise AccountingValidationError(
                "proposal_status posted is not a Billing proposal state. "
                "Wait for the AIS posting_receipt; do not expect Billing to flip "
                "the proposal to posted."
            )
        case _:
            raise AccountingValidationError(
                f"proposal_status {proposal_status} is not ingestible. "
                "Supply validated or exported, then retry ingest."
            )


def _published_proposal_status(payload: Mapping[str, object]) -> str:
    if "proposal_status" in payload:
        return str(payload["proposal_status"])
    if "proposal_status_code" in payload:
        raise AccountingValidationError(
            "published field is proposal_status, not proposal_status_code. "
            "Map the Billing contract field, then retry ingest."
        )
    raise AccountingValidationError(
        "proposal_status is required. Supply the Billing published proposal_status, "
        "then retry ingest."
    )


def _journal_proposal_from_contract(payload: Mapping[str, object]) -> JournalProposal:
    for field_name in _REQUIRED_CONTRACT_FIELDS:
        if field_name not in payload or payload[field_name] in (None, ""):
            raise AccountingValidationError(
                f"{field_name} is required. Supply the Billing published "
                f"{field_name}, then retry ingest."
            )
    source_event_references = payload["source_event_references"]
    if not isinstance(source_event_references, Sequence) or isinstance(
        source_event_references, (str, bytes)
    ):
        raise AccountingValidationError(
            "source_event_references must be an array of CWL URNs. "
            "Supply the Billing published references, then retry ingest."
        )
    lines = payload["lines"]
    if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)):
        raise AccountingValidationError(
            "lines must be an array of journal line objects. "
            "Supply the Billing published lines, then retry ingest."
        )
    return JournalProposal(
        proposal_id=str(payload["proposal_id"]),
        proposal_contract_version=int(payload["proposal_contract_version"]),
        idempotency_key=str(payload["idempotency_key"]),
        tenant_reference=str(payload["tenant_reference"]),
        legal_entity_reference=str(payload["legal_entity_reference"]),
        intended_book_role_code=str(payload["intended_book_role_code"]),
        transaction_currency=str(payload["transaction_currency"]),
        transaction_date=_require_iso_date(payload["transaction_date"], "transaction_date"),
        accounting_date=_require_iso_date(payload["accounting_date"], "accounting_date"),
        source_payload_hash=str(payload["source_payload_hash"]),
        source_event_references=tuple(str(reference) for reference in source_event_references),
        lines=tuple(_journal_line_from_contract(line) for line in lines),
    )


def _journal_line_from_contract(line: object) -> JournalLineProposal:
    if not isinstance(line, Mapping):
        raise AccountingValidationError(
            "each journal line must be a line object. "
            "Supply Billing line objects, then retry ingest."
        )
    return JournalLineProposal(
        line_number=int(line["line_number"]),
        account_role_code=str(line["account_role_code"]),
        debit_amount=str(line["debit_amount"]),
        credit_amount=str(line["credit_amount"]),
    )


def _require_iso_date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise AccountingValidationError(
            f"{field_name} must be an ISO date. Supply YYYY-MM-DD, then retry ingest."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AccountingValidationError(
            f"{field_name} must be an ISO date. Supply YYYY-MM-DD, then retry ingest."
        ) from error
