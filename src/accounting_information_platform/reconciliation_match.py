"""Persist and read proposed reconciliation matches without granting approval authority."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Mapping
from uuid import UUID

from .core import (
    AccountingValidationError,
    IdempotencyConflictError,
    _HASH_PATTERN,
    _parse_amount,
)
from .persistence import PostgresPostingLedger, _exact_amount_text
from .reconciliation_run import _parse_uuid


def accept_reconciliation_match(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Persist one exact 1:1 proposed match for an evaluating reconciliation run."""
    command = _require_command(payload, tenant_reference)
    run_id = _parse_uuid(
        str(command.get("reconciliation_run_id") or ""),
        "reconciliation_run_id",
    )
    statement_reference = _require_text(
        command.get("statement_entry_reference"), "statement_entry_reference"
    )
    journal_reference = _require_text(command.get("journal_reference"), "journal_reference")
    rule_code = _require_text(command.get("rule_code"), "rule_code")
    idempotency_key = _require_text(
        command.get("candidate_idempotency_key"), "candidate_idempotency_key"
    )
    source_payload_hash = _require_hash(command.get("source_payload_hash"))
    source_payload_reference = _require_text(
        command.get("source_payload_reference"), "source_payload_reference"
    )
    statement_amount = _require_positive_amount(
        command.get("statement_amount"), "statement_amount"
    )
    journal_amount = _require_positive_amount(command.get("journal_amount"), "journal_amount")
    if statement_amount != journal_amount:
        raise AccountingValidationError(
            "statement_amount and journal_amount must be equal for a 1:1 proposed match. "
            "Supply exact equal source amounts, then retry the match."
        )
    command_hash = _command_hash(
        tenant_reference=tenant_reference,
        reconciliation_run_id=run_id,
        statement_entry_reference=statement_reference,
        journal_reference=journal_reference,
        statement_amount=statement_amount,
        journal_amount=journal_amount,
        rule_code=rule_code,
        idempotency_key=idempotency_key,
        source_payload_hash=source_payload_hash,
        source_payload_reference=source_payload_reference,
    )

    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(
            connection, f"reconciliation_match_command:{idempotency_key}"
        )
        prior = connection.execute(
            """
            SELECT reconciliation_candidate_id, reconciliation_match_id,
                   candidate_command_hash, source_payload_hash
            FROM accounting_core.reconciliation_match_command
            WHERE tenant_account_id = %s
              AND candidate_idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior is not None:
            if prior[2] != command_hash or prior[3] != source_payload_hash:
                raise IdempotencyConflictError(
                    "candidate idempotency key was already used with different match evidence. "
                    "Supply a new candidate_idempotency_key, then retry the match."
                )
            return _load_reconciliation_match_document(
                connection,
                tenant_id,
                tenant_reference,
                prior[1],
                replayed=True,
            )

        run = connection.execute(
            """
            SELECT run_status_code
            FROM accounting_core.reconciliation_run
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
            """,
            (tenant_id, run_id),
        ).fetchone()
        if run is None:
            raise AccountingValidationError(
                "reconciliation run is not recorded for this tenant. "
                "Supply an evaluating reconciliation_run_id, then retry the match."
            )
        if run[0] != "evaluating":
            raise AccountingValidationError(
                "reconciliation matches can only be proposed on an evaluating run. "
                "Open a new evaluating reconciliation run, then retry the match."
            )

        ledger._acquire_command_lock(
            connection,
            f"reconciliation_candidate:{run_id}:{statement_reference}:{journal_reference}",
        )
        existing = connection.execute(
            """
            SELECT reconciliation_candidate_id
            FROM accounting_core.reconciliation_candidate
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
              AND statement_entry_reference = %s
              AND journal_reference = %s
            """,
            (tenant_id, run_id, statement_reference, journal_reference),
        ).fetchone()
        if existing is not None:
            raise AccountingValidationError(
                "the reconciliation candidate is already recorded for this run and source pair. "
                "Use the existing proposed match or a new source pair, then retry."
            )

        candidate_id = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_candidate (
                tenant_account_id, reconciliation_run_id,
                statement_entry_reference, journal_reference,
                statement_amount, journal_amount, rule_code
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING reconciliation_candidate_id
            """,
            (
                tenant_id,
                run_id,
                statement_reference,
                journal_reference,
                statement_amount,
                journal_amount,
                rule_code,
            ),
        ).fetchone()[0]
        match_id = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_match (
                tenant_account_id, reconciliation_run_id,
                reconciliation_candidate_id, match_status_code
            )
            VALUES (%s, %s, %s, 'proposed')
            RETURNING reconciliation_match_id
            """,
            (tenant_id, run_id, candidate_id),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO accounting_core.statement_match_allocation (
                tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                statement_entry_reference, allocated_amount
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (tenant_id, run_id, match_id, statement_reference, statement_amount),
        )
        connection.execute(
            """
            INSERT INTO accounting_core.journal_match_allocation (
                tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                journal_reference, allocated_amount
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (tenant_id, run_id, match_id, journal_reference, journal_amount),
        )
        connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_match_command (
                tenant_account_id, reconciliation_run_id,
                reconciliation_candidate_id, reconciliation_match_id,
                candidate_idempotency_key, candidate_command_hash,
                source_payload_hash, source_payload_reference
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                run_id,
                candidate_id,
                match_id,
                idempotency_key,
                command_hash,
                source_payload_hash,
                source_payload_reference,
            ),
        )
        return _load_reconciliation_match_document(
            connection,
            tenant_id,
            tenant_reference,
            match_id,
            replayed=False,
        )


def lookup_reconciliation_match(
    database_url: str, tenant_reference: str, reconciliation_match_id: str
) -> dict[str, object]:
    """Read one tenant-scoped proposed reconciliation match and its exact allocation."""
    match_id = _parse_uuid(reconciliation_match_id, "reconciliation_match_id")
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        return _load_reconciliation_match_document(
            connection,
            tenant_id,
            tenant_reference,
            match_id,
            replayed=False,
        )


def _load_reconciliation_match_document(
    connection: object,
    tenant_id: UUID,
    tenant_reference: str,
    match_id: UUID,
    *,
    replayed: bool,
) -> dict[str, object]:
    """Load one command-backed match with candidate, allocation, and provenance facts."""
    row = connection.execute(
        """
        SELECT candidate.reconciliation_candidate_id,
               match.reconciliation_match_id,
               match.reconciliation_run_id,
               candidate.statement_entry_reference,
               candidate.journal_reference,
               candidate.statement_amount,
               candidate.journal_amount,
               candidate.rule_code,
               match.match_status_code,
               command.candidate_idempotency_key,
               command.candidate_command_hash,
               command.source_payload_hash,
               command.source_payload_reference,
               statement_allocation.allocated_amount
        FROM accounting_core.reconciliation_match AS match
        JOIN accounting_core.reconciliation_candidate AS candidate
          ON candidate.tenant_account_id = match.tenant_account_id
         AND candidate.reconciliation_run_id = match.reconciliation_run_id
         AND candidate.reconciliation_candidate_id = match.reconciliation_candidate_id
        JOIN accounting_core.reconciliation_match_command AS command
          ON command.tenant_account_id = match.tenant_account_id
         AND command.reconciliation_run_id = match.reconciliation_run_id
         AND command.reconciliation_match_id = match.reconciliation_match_id
        JOIN accounting_core.statement_match_allocation AS statement_allocation
          ON statement_allocation.tenant_account_id = match.tenant_account_id
         AND statement_allocation.reconciliation_run_id = match.reconciliation_run_id
         AND statement_allocation.reconciliation_match_id = match.reconciliation_match_id
        WHERE match.tenant_account_id = %s
          AND match.reconciliation_match_id = %s
        """,
        (tenant_id, match_id),
    ).fetchone()
    if row is None:
        raise AccountingValidationError(
            "reconciliation match is not recorded for this tenant. "
            "Supply a persisted reconciliation_match_id, then retry the match read."
        )
    return {
        "tenant_reference": tenant_reference,
        "reconciliation_candidate_id": str(row[0]),
        "reconciliation_match_id": str(row[1]),
        "reconciliation_run_id": str(row[2]),
        "statement_entry_reference": row[3],
        "journal_reference": row[4],
        "statement_amount": _display_amount(row[5]),
        "journal_amount": _display_amount(row[6]),
        "allocated_amount": _display_amount(row[13]),
        "rule_code": row[7],
        "match_status_code": row[8],
        "candidate_idempotency_key": row[9],
        "candidate_command_hash": row[10],
        "source_payload_hash": row[11],
        "source_payload_reference": row[12],
        "replayed": replayed,
    }


def _require_command(payload: object, tenant_reference: str) -> Mapping[str, object]:
    """Require a tenant-bound mapping for a proposed match command."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "reconciliation match payload must be a JSON object. "
            "Supply a proposed reconciliation match, then retry."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "reconciliation match tenant_reference does not match the bound tenant. "
            "Send the match to that tenant's AIS endpoint, then retry."
        )
    return payload


def _require_text(value: object, field_name: str) -> str:
    """Require one canonical non-empty command text field."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise AccountingValidationError(
            f"{field_name} is required and must be a canonical non-empty string. "
            f"Supply {field_name}, then retry the match."
        )
    return value


def _require_hash(value: object) -> str:
    """Require one immutable source-payload SHA-256 digest."""
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError(
            "source_payload_hash must be a canonical sha256 digest. "
            "Supply the immutable match-evidence hash, then retry the match."
        )
    return value


def _require_positive_amount(value: object, field_name: str) -> Decimal:
    """Require a quoted positive exact decimal for persisted match evidence."""
    if not isinstance(value, str):
        raise AccountingValidationError(
            f"{field_name} must be a quoted exact decimal string. "
            f"Supply {field_name} as a decimal string, then retry the match."
        )
    amount = _parse_amount(value)
    if amount <= 0:
        raise AccountingValidationError(
            f"{field_name} must be greater than zero. Supply a positive exact amount, then retry the match."
        )
    return amount


def _display_amount(value: Decimal) -> str:
    """Render a database numeric as the canonical decimal string used by reads."""
    text = _exact_amount_text(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _command_hash(
    *,
    tenant_reference: str,
    reconciliation_run_id: UUID,
    statement_entry_reference: str,
    journal_reference: str,
    statement_amount: Decimal,
    journal_amount: Decimal,
    rule_code: str,
    idempotency_key: str,
    source_payload_hash: str,
    source_payload_reference: str,
) -> str:
    """Return the canonical hash for one proposed match command."""
    payload = {
        "candidate_idempotency_key": idempotency_key,
        "journal_amount": _exact_amount_text(journal_amount),
        "journal_reference": journal_reference,
        "reconciliation_run_id": str(reconciliation_run_id),
        "rule_code": rule_code,
        "source_payload_hash": source_payload_hash,
        "source_payload_reference": source_payload_reference,
        "statement_amount": _exact_amount_text(statement_amount),
        "statement_entry_reference": statement_entry_reference,
        "tenant_reference": tenant_reference,
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = ["accept_reconciliation_match", "lookup_reconciliation_match"]
