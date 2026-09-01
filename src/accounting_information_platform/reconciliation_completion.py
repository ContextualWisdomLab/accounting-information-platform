"""Complete a reconciliation run only from one database-owned evidence snapshot.

The command is deliberately narrower than a generic run-status mutation. It may
move an ``evaluating`` or ``review_required`` run to ``reconciled`` only after
PostgreSQL-owned population, approval, exception, and exact book-to-bank bridge
evidence has been validated in one consistent transaction. The command does not
post or reverse journals, close a fiscal period, or alter accounting policy.
"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping
from uuid import UUID

from .core import (
    AccountingValidationError,
    IdempotencyConflictError,
    _require_reference,
)
from .persistence import PostgresPostingLedger
from .reconciliation_close_package import _database_owned_close_projection_evidence

_COMPLETION_PURPOSE_CODE = "reconciliation_close_review"
_COMPLETION_NEXT_ACTION = (
    "Build and archive the database-authoritative reconciliation close package, "
    "then obtain the separately authorized period-close decision."
)


def _canonical_sha256(payload: object) -> str:
    """Return a deterministic SHA-256 identity for one JSON-compatible evidence value."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _parse_uuid(value: object, field_name: str) -> UUID:
    """Return one UUID command identity or fail before database access."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise AccountingValidationError(
            f"{field_name} must be a canonical UUID string. Supply the recorded run identity, then retry."
        )
    try:
        return UUID(value)
    except ValueError as exc:
        raise AccountingValidationError(
            f"{field_name} must be a canonical UUID string. Supply the recorded run identity, then retry."
        ) from exc


def _require_completion_command(
    payload: object,
    tenant_reference: str,
) -> tuple[UUID, str, str, str]:
    """Validate the purpose-bound public completion command before persistence."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "reconciliation completion payload must be a JSON object. Supply the completion command, then retry."
        )
    payload_tenant = payload.get("tenant_reference")
    if payload_tenant != tenant_reference:
        raise AccountingValidationError(
            "tenant_reference must match the bound accounting tenant. Use the tenant-scoped endpoint, then retry."
        )
    run_id = _parse_uuid(payload.get("reconciliation_run_id"), "reconciliation_run_id")
    completion_key = payload.get("reconciliation_completion_key")
    if (
        not isinstance(completion_key, str)
        or not completion_key
        or completion_key.strip() != completion_key
    ):
        raise AccountingValidationError(
            "reconciliation_completion_key is required and must be a canonical non-empty string. "
            "Supply a stable idempotency key, then retry."
        )
    actor_reference = payload.get("actor_reference")
    if not isinstance(actor_reference, str):
        raise AccountingValidationError(
            "actor_reference is required. Supply the accountable reconciliation reviewer, then retry."
        )
    _require_reference(actor_reference, "reconciliation completion actor reference")
    completion_purpose_code = payload.get("completion_purpose_code")
    if completion_purpose_code != _COMPLETION_PURPOSE_CODE:
        raise AccountingValidationError(
            "completion_purpose_code must be reconciliation_close_review. "
            "Use the purpose-bound reconciliation completion command, then retry."
        )
    return run_id, completion_key, actor_reference, completion_purpose_code


def _load_completion_document(
    connection: object,
    tenant_account_id: object,
    tenant_reference: str,
    completion_key: str,
    *,
    replayed: bool,
) -> dict[str, object] | None:
    """Load one immutable completion command by tenant-scoped idempotency key."""
    row = connection.execute(
        """
        SELECT reconciliation_completion_command_id::text,
               reconciliation_run_id::text,
               reconciliation_completion_key,
               completion_command_hash,
               statement_population_hash,
               book_population_hash,
               approval_population_hash,
               bridge_evidence_hash,
               actor_reference,
               completion_purpose_code,
               recorded_at
        FROM accounting_core.reconciliation_completion_command
        WHERE tenant_account_id = %s
          AND reconciliation_completion_key = %s
        """,
        (tenant_account_id, completion_key),
    ).fetchone()
    if row is None:
        return None
    return {
        "tenant_reference": tenant_reference,
        "reconciliation_completion_command_id": row[0],
        "reconciliation_run_id": row[1],
        "reconciliation_completion_key": row[2],
        "completion_command_hash": row[3],
        "statement_population_hash": row[4],
        "book_population_hash": row[5],
        "approval_population_hash": row[6],
        "bridge_evidence_hash": row[7],
        "actor_reference": row[8],
        "completion_purpose_code": row[9],
        "run_status_code": "reconciled",
        "recorded_at": row[10].isoformat().replace("+00:00", "Z"),
        "next_action": _COMPLETION_NEXT_ACTION,
        "replayed": replayed,
    }


def accept_reconciliation_completion(
    payload: object,
    database_url: str,
    tenant_reference: str,
) -> dict[str, object]:
    """Lawfully move one evidence-complete reconciliation run to ``reconciled``.

    An exact retry replays the immutable command. A changed command under the
    same key fails closed. A second key cannot replace the command that already
    reconciled the run.
    """
    run_id, completion_key, actor_reference, completion_purpose_code = (
        _require_completion_command(payload, tenant_reference)
    )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._consistent_read_session() as connection:
        tenant_account_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(
            connection,
            f"reconciliation_completion_key:{completion_key}",
        )
        prior = _load_completion_document(
            connection,
            tenant_account_id,
            tenant_reference,
            completion_key,
            replayed=True,
        )
        if prior is not None:
            if (
                prior["reconciliation_run_id"] != str(run_id)
                or prior["actor_reference"] != actor_reference
                or prior["completion_purpose_code"] != completion_purpose_code
            ):
                raise IdempotencyConflictError(
                    "reconciliation completion key was already used with different command evidence. "
                    "Supply a new reconciliation_completion_key, then retry."
                )
            return prior

        run_row = connection.execute(
            """
            SELECT run_record.run_status_code,
                   run_command.reconciliation_command_hash
            FROM accounting_core.reconciliation_run AS run_record
            JOIN accounting_core.reconciliation_run_command AS run_command
              ON run_command.tenant_account_id = run_record.tenant_account_id
             AND run_command.reconciliation_run_id = run_record.reconciliation_run_id
            WHERE run_record.tenant_account_id = %s
              AND run_record.reconciliation_run_id = %s
            FOR UPDATE OF run_record
            FOR SHARE OF run_command
            """,
            (tenant_account_id, run_id),
        ).fetchone()
        if run_row is None:
            raise AccountingValidationError(
                "reconciliation run is not recorded for this tenant. "
                "Supply a persisted reconciliation_run_id, then retry completion."
            )
        if run_row[0] not in {"evaluating", "review_required"}:
            raise AccountingValidationError(
                "reconciliation run is not eligible for completion from its current state. "
                "Use an evaluating or review_required run, then retry."
            )

        open_exception = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM accounting_core.reconciliation_exception AS exception_record
                WHERE exception_record.tenant_account_id = %s
                  AND exception_record.reconciliation_run_id = %s
                  AND exception_record.resolution_status_code = 'open'
            )
            """,
            (tenant_account_id, run_id),
        ).fetchone()[0]
        if open_exception:
            raise AccountingValidationError(
                "reconciliation run still has an open exception. Resolve or supersede every exception, then retry."
            )

        proposed_match = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM accounting_core.reconciliation_match AS match_record
                WHERE match_record.tenant_account_id = %s
                  AND match_record.reconciliation_run_id = %s
                  AND match_record.match_status_code = 'proposed'
            )
            """,
            (tenant_account_id, run_id),
        ).fetchone()[0]
        if proposed_match:
            raise AccountingValidationError(
                "reconciliation run still has a proposed match awaiting review. "
                "Approve, reject, or supersede every proposal, then retry."
            )

        bridge = _database_owned_close_projection_evidence(
            connection,
            tenant_account_id,
            reconciliation_run_reference=str(run_id),
        )
        approval_rows = connection.execute(
            """
            SELECT match_record.reconciliation_match_id::text,
                   approval.reconciliation_snapshot_hash,
                   approval.source_payload_hash,
                   approval.source_payload_reference
            FROM accounting_core.reconciliation_match AS match_record
            JOIN accounting_core.reconciliation_approval AS approval
              ON approval.tenant_account_id = match_record.tenant_account_id
             AND approval.reconciliation_run_id = match_record.reconciliation_run_id
             AND approval.reconciliation_match_id = match_record.reconciliation_match_id
            WHERE match_record.tenant_account_id = %s
              AND match_record.reconciliation_run_id = %s
              AND match_record.match_status_code = 'approved'
              AND approval.approval_decision_code = 'approved'
            ORDER BY match_record.reconciliation_match_id
            FOR SHARE OF match_record, approval
            """,
            (tenant_account_id, run_id),
        ).fetchall()
        approval_population_hash = _canonical_sha256(
            [tuple(str(value) for value in row) for row in approval_rows]
        )
        bridge_evidence_hash = _canonical_sha256(
            {
                "statement_population_hash": bridge.statement_population_reference,
                "book_population_hash": bridge.book_population_reference,
                "statement_opening_balance": str(bridge.statement_opening_balance),
                "statement_period_movements": str(bridge.statement_period_movements),
                "statement_closing_balance": str(bridge.statement_closing_balance),
                "book_opening_balance": str(bridge.book_opening_balance),
                "posted_cash_book_movements": str(bridge.posted_cash_book_movements),
                "book_closing_balance": str(bridge.book_closing_balance),
                "reconciled_book_balance": str(bridge.reconciled_book_balance),
                "outstanding_bank_items": str(bridge.outstanding_bank_items),
                "outstanding_book_items": str(bridge.outstanding_book_items),
                "unexplained_difference": str(bridge.unexplained_difference),
            }
        )
        completion_command_hash = _canonical_sha256(
            {
                "tenant_reference": tenant_reference,
                "reconciliation_run_id": str(run_id),
                "reconciliation_run_command_hash": str(run_row[1]),
                "reconciliation_completion_key": completion_key,
                "actor_reference": actor_reference,
                "completion_purpose_code": completion_purpose_code,
                "statement_population_hash": bridge.statement_population_reference,
                "book_population_hash": bridge.book_population_reference,
                "approval_population_hash": approval_population_hash,
                "bridge_evidence_hash": bridge_evidence_hash,
            }
        )
        command_id = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_completion_command (
                tenant_account_id,
                reconciliation_run_id,
                reconciliation_completion_key,
                completion_command_hash,
                statement_population_hash,
                book_population_hash,
                approval_population_hash,
                bridge_evidence_hash,
                actor_reference,
                completion_purpose_code
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING reconciliation_completion_command_id
            """,
            (
                tenant_account_id,
                run_id,
                completion_key,
                completion_command_hash,
                bridge.statement_population_reference,
                bridge.book_population_reference,
                approval_population_hash,
                bridge_evidence_hash,
                actor_reference,
                completion_purpose_code,
            ),
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE accounting_core.reconciliation_run
            SET run_status_code = 'reconciled'
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
              AND run_status_code IN ('evaluating', 'review_required')
            """,
            (tenant_account_id, run_id),
        )
        connection.execute(
            """
            INSERT INTO accounting_integration.outbox_event (
                tenant_account_id,
                event_type_code,
                aggregate_reference,
                payload_reference,
                payload_hash
            )
            VALUES (%s, 'reconciliation_run.reconciled', %s, %s, %s)
            """,
            (
                tenant_account_id,
                str(run_id),
                f"urn:cwl:reconciliation_completion:{command_id}",
                completion_command_hash,
            ),
        )
        document = _load_completion_document(
            connection,
            tenant_account_id,
            tenant_reference,
            completion_key,
            replayed=False,
        )
        if document is None:  # pragma: no cover - INSERT ... RETURNING proves existence
            raise RuntimeError("reconciliation completion command was not persisted")
        return document


__all__ = ["accept_reconciliation_completion"]
