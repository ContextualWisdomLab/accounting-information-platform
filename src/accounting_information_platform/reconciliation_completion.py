"""Evidence-derived completion for tenant-scoped bank reconciliation runs."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping
from uuid import UUID

from .core import AccountingValidationError, IdempotencyConflictError, _HASH_PATTERN
from .persistence import PostgresPostingLedger, _format_timestamp
from .reconciliation_close_package import _database_owned_close_projection_evidence
from .reconciliation_run import _load_reconciliation_run_document, _parse_uuid

_COMPLETION_NEXT_ACTION = (
    "Build the reconciliation close package from this exact reconciled run before period close."
)


def accept_reconciliation_run_completion(
    payload: object,
    database_url: str,
    tenant_reference: str,
) -> dict[str, object]:
    """Mark one run reconciled only from locked PostgreSQL review and bridge evidence."""
    command = _require_completion_command(payload, tenant_reference)
    run_id = _parse_uuid(
        str(command.get("reconciliation_run_id") or ""),
        "reconciliation_run_id",
    )
    idempotency_key = command.get("completion_idempotency_key")
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key
        or idempotency_key != idempotency_key.strip()
    ):
        raise AccountingValidationError(
            "completion_idempotency_key is required and must be a canonical non-empty string. "
            "Supply the reconciliation completion command key, then retry."
        )

    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._consistent_read_session() as connection:
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(
            connection,
            f"reconciliation_completion_key:{idempotency_key}",
        )
        connection.execute(
            "SELECT accounting_core.lock_reconciliation_run_lifecycle(%s, %s)",
            (tenant_id, run_id),
        )

        prior_by_key = _load_completion_by_key(
            connection,
            tenant_id,
            idempotency_key,
        )
        if prior_by_key is not None:
            if prior_by_key[1] != run_id:
                raise IdempotencyConflictError(
                    "completion idempotency key was already used for a different reconciliation run. "
                    "Supply a new completion_idempotency_key, then retry."
                )
            return _completion_document(
                connection,
                tenant_id,
                tenant_reference,
                prior_by_key,
                replayed=True,
            )

        prior_by_run = _load_completion_by_run(connection, tenant_id, run_id)
        if prior_by_run is not None:
            raise IdempotencyConflictError(
                "reconciliation run was already completed with a different command key. "
                "Read the reconciled run and reuse its recorded completion evidence."
            )

        run_row = connection.execute(
            """
            SELECT run_status_code
            FROM accounting_core.reconciliation_run
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id),
        ).fetchone()
        if run_row is None:
            raise AccountingValidationError(
                "reconciliation run is not recorded for this tenant. "
                "Supply a persisted reconciliation_run_id, then retry completion."
            )
        prior_status = str(run_row[0])
        if prior_status not in {"evaluating", "review_required"}:
            raise AccountingValidationError(
                "reconciliation run must be evaluating or review_required before completion. "
                "Create a new run instead of reopening terminal reconciliation evidence."
            )

        match_rows = connection.execute(
            """
            SELECT match.reconciliation_match_id::text,
                   match.match_status_code,
                   approval.approval_decision_code,
                   approval.reconciliation_snapshot_hash
            FROM accounting_core.reconciliation_match AS match
            LEFT JOIN accounting_core.reconciliation_approval AS approval
              ON approval.tenant_account_id = match.tenant_account_id
             AND approval.reconciliation_run_id = match.reconciliation_run_id
             AND approval.reconciliation_match_id = match.reconciliation_match_id
            WHERE match.tenant_account_id = %s
              AND match.reconciliation_run_id = %s
            ORDER BY match.reconciliation_match_id
            FOR SHARE OF match, approval
            """,
            (tenant_id, run_id),
        ).fetchall()
        if any(str(row[1]) == "proposed" for row in match_rows):
            raise AccountingValidationError(
                "proposed reconciliation matches remain in this run. "
                "Approve, reject, or supersede every proposal, then retry completion."
            )
        approved_rows = tuple(row for row in match_rows if str(row[1]) == "approved")
        if not approved_rows:
            raise AccountingValidationError(
                "no approved reconciliation match exists for this run. "
                "Review deterministic match proposals before retrying completion."
            )
        for row in approved_rows:
            if (
                str(row[2]) != "approved"
                or not isinstance(row[3], str)
                or _HASH_PATTERN.fullmatch(row[3]) is None
            ):
                raise AccountingValidationError(
                    "approved match is missing its immutable database approval snapshot. "
                    "Repair the review evidence before retrying completion."
                )

        exception_rows = connection.execute(
            """
            SELECT reconciliation_exception_id::text, resolution_status_code
            FROM accounting_core.reconciliation_exception
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
            ORDER BY reconciliation_exception_id
            FOR SHARE
            """,
            (tenant_id, run_id),
        ).fetchall()
        if any(str(row[1]) == "open" for row in exception_rows):
            raise AccountingValidationError(
                "open reconciliation exceptions remain in this run. "
                "Resolve or supersede each exception, then retry completion."
            )

        try:
            bridge = _database_owned_close_projection_evidence(
                connection,
                tenant_id,
                reconciliation_run_reference=str(run_id),
            )
        except ValueError as error:
            raise AccountingValidationError(
                f"reconciliation bridge evidence is incomplete: {error}. "
                "Repair the immutable statement/book evidence, then retry completion."
            ) from error

        completion_snapshot_hash = _completion_snapshot_hash(
            tenant_reference=tenant_reference,
            run_id=run_id,
            bridge=bridge,
            match_rows=match_rows,
            exception_rows=exception_rows,
        )
        completion_row = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run_completion_command (
                tenant_account_id,
                reconciliation_run_id,
                completion_idempotency_key,
                prior_run_status_code,
                completion_snapshot_hash,
                statement_population_reference,
                book_population_reference
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING reconciliation_run_completion_command_id,
                      reconciliation_run_id,
                      completion_idempotency_key,
                      prior_run_status_code,
                      completion_snapshot_hash,
                      statement_population_reference,
                      book_population_reference,
                      completed_at
            """,
            (
                tenant_id,
                run_id,
                idempotency_key,
                prior_status,
                completion_snapshot_hash,
                bridge.statement_population_reference,
                bridge.book_population_reference,
            ),
        ).fetchone()
        connection.execute(
            """
            UPDATE accounting_core.reconciliation_run
            SET run_status_code = 'reconciled'
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
              AND run_status_code = %s
            """,
            (tenant_id, run_id, prior_status),
        )
        return _completion_document(
            connection,
            tenant_id,
            tenant_reference,
            completion_row,
            replayed=False,
        )


def _require_completion_command(
    payload: object,
    tenant_reference: str,
) -> Mapping[str, object]:
    """Validate the minimal command envelope without accepting caller-owned status facts."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "reconciliation completion payload must be a JSON object. "
            "Supply a completion command, then retry."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "reconciliation completion tenant_reference does not match the bound tenant. "
            "Send the command to that tenant's AIS endpoint, then retry."
        )
    return payload


def _load_completion_by_key(
    connection: object,
    tenant_id: UUID,
    idempotency_key: str,
) -> tuple[object, ...] | None:
    """Return immutable completion evidence for one tenant command key."""
    return connection.execute(
        """
        SELECT reconciliation_run_completion_command_id,
               reconciliation_run_id,
               completion_idempotency_key,
               prior_run_status_code,
               completion_snapshot_hash,
               statement_population_reference,
               book_population_reference,
               completed_at
        FROM accounting_core.reconciliation_run_completion_command
        WHERE tenant_account_id = %s
          AND completion_idempotency_key = %s
        """,
        (tenant_id, idempotency_key),
    ).fetchone()


def _load_completion_by_run(
    connection: object,
    tenant_id: UUID,
    run_id: UUID,
) -> tuple[object, ...] | None:
    """Return the single immutable completion command already bound to a run."""
    return connection.execute(
        """
        SELECT reconciliation_run_completion_command_id,
               reconciliation_run_id,
               completion_idempotency_key,
               prior_run_status_code,
               completion_snapshot_hash,
               statement_population_reference,
               book_population_reference,
               completed_at
        FROM accounting_core.reconciliation_run_completion_command
        WHERE tenant_account_id = %s
          AND reconciliation_run_id = %s
        """,
        (tenant_id, run_id),
    ).fetchone()


def _completion_snapshot_hash(
    *,
    tenant_reference: str,
    run_id: UUID,
    bridge: object,
    match_rows: list[tuple[object, ...]],
    exception_rows: list[tuple[object, ...]],
) -> str:
    """Hash the exact database evidence that authorized one run completion."""
    payload = {
        "book_population_reference": bridge.book_population_reference,
        "book_closing_balance": format(bridge.book_closing_balance, "f"),
        "exception_states": [
            [str(row[0]), str(row[1])] for row in exception_rows
        ],
        "match_states": [
            [
                str(row[0]),
                str(row[1]),
                "" if row[2] is None else str(row[2]),
                "" if row[3] is None else str(row[3]),
            ]
            for row in match_rows
        ],
        "reconciliation_run_id": str(run_id),
        "statement_closing_balance": format(bridge.statement_closing_balance, "f"),
        "statement_population_reference": bridge.statement_population_reference,
        "tenant_reference": tenant_reference,
        "version": 1,
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _completion_document(
    connection: object,
    tenant_id: UUID,
    tenant_reference: str,
    completion_row: tuple[object, ...],
    *,
    replayed: bool,
) -> dict[str, object]:
    """Return one completion receipt with the authoritative run read model."""
    run_document = _load_reconciliation_run_document(
        connection,
        tenant_id,
        tenant_reference,
        completion_row[1],
        replayed=replayed,
    )
    return {
        **run_document,
        "reconciliation_run_completion_command_id": str(completion_row[0]),
        "completion_idempotency_key": str(completion_row[2]),
        "prior_run_status_code": str(completion_row[3]),
        "completion_snapshot_hash": str(completion_row[4]),
        "statement_population_reference": str(completion_row[5]),
        "book_population_reference": str(completion_row[6]),
        "completed_at": _format_timestamp(completion_row[7]),
        "next_action": _COMPLETION_NEXT_ACTION,
        "replayed": replayed,
    }


__all__ = ["accept_reconciliation_run_completion"]
