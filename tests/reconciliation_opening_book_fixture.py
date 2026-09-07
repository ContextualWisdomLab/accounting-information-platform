"""Shared real-PostgreSQL fixtures for reconciliation database authority."""

from __future__ import annotations

from datetime import date
from typing import Any
import uuid

from accounting_information_platform import JournalLineProposal, JournalProposal


def post_reconciliation_opening_book_balance(case: Any) -> None:
    """Post the statement opening book balance through the normal ledger path."""
    proposal_id = str(uuid.uuid4())
    source_payload_hash = "sha256:" + uuid.uuid4().hex * 2
    proposal = JournalProposal(
        proposal_id=proposal_id,
        proposal_contract_version=1,
        idempotency_key=(
            f"{case.policy.tenant_reference}:reconciliation_opening_book:"
            f"{proposal_id}:{source_payload_hash}:v1"
        ),
        tenant_reference=case.policy.tenant_reference,
        legal_entity_reference=case.policy.legal_entity_reference,
        intended_book_role_code=case.policy.intended_book_role_code,
        transaction_currency=case.policy.transaction_currency,
        transaction_date=date(2026, 8, 22),
        accounting_date=date(2026, 8, 22),
        source_payload_hash=source_payload_hash,
        source_event_references=(
            f"urn:cwl:accounting:test:reconciliation_opening_book:{proposal_id}",
        ),
        lines=(
            JournalLineProposal(
                line_number=1,
                account_role_code="cash_receipt",
                debit_amount="100000",
                credit_amount="0",
            ),
            JournalLineProposal(
                line_number=2,
                account_role_code="unapplied_cash",
                debit_amount="0",
                credit_amount="100000",
            ),
        ),
    )
    receipt = case.ledger.post(proposal, case.policy)
    if receipt.posting_status_code != "posted":
        raise AssertionError("opening-book fixture did not post through the authoritative ledger")
