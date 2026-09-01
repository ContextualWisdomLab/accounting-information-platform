"""Evidence-derived lifecycle transition for tenant-scoped reconciliation runs.

A lifecycle command can mark one reviewed run ``reconciled`` only after the
PostgreSQL-owned statement/book populations form an exact bridge, every terminal
match has durable decision evidence, and no open exception remains. This module
cannot post or reverse journals, close periods, or change accounting policy.
"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping
from uuid import UUID

from .core import (
    AccountingValidationError,
    IdempotencyConflictError,
    _require_code,
    _require_reference,
)
from .persistence import PostgresPostingLedger, _format_timestamp
from .reconciliation_run import _parse_timestamp, _parse_uuid

_RECONCILED_NEXT_ACTION = (
    "Use this reconciled run as review evidence; period close still requires its "
    "separately authorized close command."
)
_TRANSITION_HASH_SENTINEL = "sha256:" + "0" * 64


def reconcile_reconciliation_run(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Transition one run to ``reconciled`` from database-owned evidence.

    The run lifecycle advisory lock is acquired before the first MVCC snapshot,
    then source and review state are evaluated under PostgreSQL ``REPEATABLE
    READ``. Exact retries replay immutable command evidence; changed retries fail
    closed.
    """
    command = _require_transition_command(payload, tenant_reference)
    run_id = _parse_uuid(
        str(command.get("reconciliation_run_id") or ""), "reconciliation_run_id"
    )
    idempotency_key = _canonical_text(
        command.get("reconciliation_idempotency_key"),
        "reconciliation_idempotency_key",
    )
    actor_reference = _canonical_text(command.get("actor_reference"), "actor_reference")
    purpose_code = _canonical_text(command.get("purpose_code"), "purpose_code")
    _require_reference(actor_reference, "actor reference")
    _require_code(purpose_code, "purpose code")
    effective_at = _parse_timestamp(str(command.get("effective_at") or ""), "effective_at")

    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        # SET TRANSACTION itself does not establish the data snapshot. Acquire
        # the lifecycle lock next, before tenant or reconciliation rows are read.
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        ledger._acquire_command_lock(connection, f"reconciliation_run_lifecycle:{run_id}")
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(
            connection, f"reconciliation_run_transition_key:{idempotency_key}"
        )

        prior = connection.execute(
            """
            SELECT reconciliation_run_id, actor_reference, purpose_code, effective_at
            FROM accounting_core.reconciliation_run_transition_command
            WHERE tenant_account_id = %s
              AND reconciliation_transition_idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior is not None:
            if prior != (run_id, actor_reference, purpose_code, effective_at):
                raise IdempotencyConflictError(
                    "reconciliation lifecycle idempotency key was already used with different "
                    "transition evidence. Supply a new reconciliation_idempotency_key, then retry."
                )
            return _load_transition_document(
                connection,
                tenant_id,
                tenant_reference,
                run_id,
                idempotency_key,
                replayed=True,
            )

        opening_key = connection.execute(
            """
            SELECT 1
            FROM accounting_core.reconciliation_run_command
            WHERE tenant_account_id = %s
              AND reconciliation_idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if opening_key is not None:
            raise IdempotencyConflictError(
                "reconciliation lifecycle idempotency key is already the run-opening command key. "
                "Supply a distinct reconciliation_idempotency_key, then retry."
            )

        run_row = connection.execute(
            """
            SELECT run_status_code, currency_code
            FROM accounting_core.reconciliation_run
            WHERE tenant_account_id = %s AND reconciliation_run_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id),
        ).fetchone()
        if run_row is None:
            raise AccountingValidationError(
                "reconciliation run is not recorded for this tenant. Supply a persisted "
                "reconciliation_run_id, then retry the lifecycle transition."
            )
        current_status = str(run_row[0])
        if current_status == "reconciled":
            existing = connection.execute(
                """
                SELECT reconciliation_transition_idempotency_key
                FROM accounting_core.reconciliation_run_transition_command
                WHERE tenant_account_id = %s AND reconciliation_run_id = %s
                  AND target_run_status_code = 'reconciled'
                """,
                (tenant_id, run_id),
            ).fetchone()
            if existing is None:
                raise AccountingValidationError(
                    "reconciliation run is reconciled without durable lifecycle command evidence. "
                    "Restore the original evidence through an audited migration before using the run."
                )
            raise IdempotencyConflictError(
                "reconciliation run is already reconciled under another lifecycle command. "
                f"Replay reconciliation_idempotency_key {existing[0]!r} instead."
            )
        if current_status not in {"evaluating", "review_required"}:
            raise AccountingValidationError(
                f"reconciliation run is {current_status}; only evaluating or review_required runs "
                "can transition to reconciled. Create or restore the reviewed run, then retry."
            )

        match_state, exception_state = _load_review_control_state(connection, tenant_id, run_id)
        _validate_review_control_state(match_state, exception_state)

        from .reconciliation_close_package import (  # pylint: disable=import-outside-toplevel
            _database_owned_close_projection_evidence,
        )

        try:
            bridge = _database_owned_close_projection_evidence(
                connection, tenant_id, reconciliation_run_reference=str(run_id)
            )
        except (ValueError, ArithmeticError) as error:
            raise AccountingValidationError(
                "reconciliation run cannot be finalized because its database-owned book-to-bank "
                "bridge does not tie exactly. Resolve the source difference or exception, then retry."
            ) from error
        authoritative_currency_code = str(run_row[1] or "")
        if not authoritative_currency_code:
            raise AccountingValidationError(
                "reconciliation run currency evidence is missing. Restore the immutable run scope, then retry."
            )

        opening_command = connection.execute(
            """
            SELECT reconciliation_command_hash
            FROM accounting_core.reconciliation_run_command
            WHERE tenant_account_id = %s AND reconciliation_run_id = %s
            """,
            (tenant_id, run_id),
        ).fetchone()
        if opening_command is None:
            raise AccountingValidationError(
                "reconciliation run has no immutable opening-command evidence. Restore retained "
                "command provenance through an audited migration, then retry."
            )
        snapshot_hash = _transition_snapshot_hash(
            run_id,
            str(opening_command[0]),
            bridge,
            match_state,
            exception_state,
            currency_code=authoritative_currency_code,
        )
        transition_id, transition_hash, _recorded_at = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run_transition_command (
                tenant_account_id, reconciliation_run_id,
                reconciliation_transition_idempotency_key, target_run_status_code,
                reconciliation_snapshot_hash, statement_population_reference,
                book_population_reference, reconciliation_transition_command_hash,
                actor_reference, purpose_code, effective_at
            )
            VALUES (%s, %s, %s, 'reconciled', %s, %s, %s, %s, %s, %s, %s)
            RETURNING reconciliation_run_transition_command_id,
                      reconciliation_transition_command_hash, recorded_at
            """,
            (
                tenant_id,
                run_id,
                idempotency_key,
                snapshot_hash,
                bridge.statement_population_reference,
                bridge.book_population_reference,
                _TRANSITION_HASH_SENTINEL,
                actor_reference,
                purpose_code,
                effective_at,
            ),
        ).fetchone()
        if transition_hash == _TRANSITION_HASH_SENTINEL:
            raise AccountingValidationError(
                "reconciliation transition command hash was not assigned by the database. "
                "Restore the reconciliation lifecycle trigger, verify the migration, then retry."
            )
        connection.execute(
            """
            UPDATE accounting_core.reconciliation_run
            SET run_status_code = 'reconciled'
            WHERE tenant_account_id = %s AND reconciliation_run_id = %s
            """,
            (tenant_id, run_id),
        )
        connection.execute(
            """
            INSERT INTO accounting_integration.outbox_event (
                tenant_account_id, event_type_code, aggregate_reference,
                payload_reference, payload_hash
            )
            VALUES (%s, 'reconciliation_run_reconciled', %s, %s, %s)
            """,
            (
                tenant_id,
                f"urn:cwl:accounting:reconciliation_run:{run_id}",
                f"urn:cwl:accounting:reconciliation_run_transition:{transition_id}",
                transition_hash,
            ),
        )
        return _load_transition_document(
            connection,
            tenant_id,
            tenant_reference,
            run_id,
            idempotency_key,
            replayed=False,
        )


def _require_transition_command(payload: object, tenant_reference: str) -> Mapping[str, object]:
    """Return one lifecycle command bound to the endpoint tenant."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "reconciliation lifecycle payload must be a JSON object. Supply the transition command, then retry."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "reconciliation lifecycle tenant_reference does not match the bound tenant. "
            "Send the command to that tenant's AIS endpoint, then retry."
        )
    if payload.get("reconciliation_action_code") != "reconcile":
        raise AccountingValidationError(
            "reconciliation_action_code must be reconcile. Supply the supported lifecycle action, then retry."
        )
    return payload


def _canonical_text(value: object, field_name: str) -> str:
    """Require a non-empty canonical string without surrounding whitespace."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise AccountingValidationError(
            f"{field_name} is required and must be a canonical non-empty string. "
            f"Supply a valid {field_name}, then retry."
        )
    return value


def _load_review_control_state(
    connection: object, tenant_id: UUID, run_id: UUID
) -> tuple[tuple[tuple[str, str, str, str], ...], tuple[tuple[str, str, str], ...]]:
    """Load complete reviewed-match and exception populations in stable order."""
    match_rows = connection.execute(
        """
        SELECT reviewed_match.reconciliation_match_id::text,
               reviewed_match.match_status_code,
               COALESCE(approval.approval_decision_code, ''),
               COALESCE(approval.reconciliation_snapshot_hash, '')
        FROM accounting_core.reconciliation_match AS reviewed_match
        LEFT JOIN accounting_core.reconciliation_approval AS approval
          ON approval.tenant_account_id = reviewed_match.tenant_account_id
         AND approval.reconciliation_run_id = reviewed_match.reconciliation_run_id
         AND approval.reconciliation_match_id = reviewed_match.reconciliation_match_id
        WHERE reviewed_match.tenant_account_id = %s
          AND reviewed_match.reconciliation_run_id = %s
        ORDER BY reviewed_match.reconciliation_match_id
        """,
        (tenant_id, run_id),
    ).fetchall()
    exception_rows = connection.execute(
        """
        SELECT reconciliation_exception_id::text, exception_code, resolution_status_code
        FROM accounting_core.reconciliation_exception
        WHERE tenant_account_id = %s AND reconciliation_run_id = %s
        ORDER BY reconciliation_exception_id
        """,
        (tenant_id, run_id),
    ).fetchall()
    return (
        tuple((str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in match_rows),
        tuple((str(row[0]), str(row[1]), str(row[2])) for row in exception_rows),
    )


def _validate_review_control_state(
    match_state: tuple[tuple[str, str, str, str], ...],
    exception_state: tuple[tuple[str, str, str], ...],
) -> None:
    """Reject incomplete reviews, mismatched approvals, and unresolved exceptions."""
    for match_reference, status_code, decision_code, snapshot_hash in match_state:
        if status_code == "proposed":
            raise AccountingValidationError(
                f"reconciliation match {match_reference} still requires review. Approve, reject, "
                "or supersede the proposal, then retry reconciliation."
            )
        if status_code in {"approved", "rejected"} and (
            decision_code != status_code or not snapshot_hash
        ):
            raise AccountingValidationError(
                f"reconciliation match {match_reference} lacks decision-consistent immutable approval evidence. "
                "Restore the reviewed decision evidence, then retry reconciliation."
            )
    for exception_reference, exception_code, resolution_status in exception_state:
        if resolution_status == "open":
            raise AccountingValidationError(
                f"reconciliation exception {exception_reference} ({exception_code}) is still open. "
                "Resolve or supersede it with retained evidence, then retry reconciliation."
            )


def _transition_snapshot_hash(
    run_id: UUID,
    run_command_hash: str,
    bridge: object,
    match_state: tuple[tuple[str, str, str, str], ...],
    exception_state: tuple[tuple[str, str, str], ...],
    *,
    currency_code: str | None = None,
) -> str:
    """Bind run scope, exact populations, bridge arithmetic, and review state to one digest."""
    authoritative_currency_code = (
        currency_code if currency_code is not None else str(bridge.currency_code)
    )
    payload = {
        "book_closing_balance": str(bridge.book_closing_balance),
        "book_opening_balance": str(bridge.book_opening_balance),
        "book_population_reference": bridge.book_population_reference,
        "currency_code": authoritative_currency_code,
        "exception_state": exception_state,
        "match_state": match_state,
        "outstanding_bank_items": str(bridge.outstanding_bank_items),
        "outstanding_book_items": str(bridge.outstanding_book_items),
        "posted_cash_book_movements": str(bridge.posted_cash_book_movements),
        "reconciled_book_balance": str(bridge.reconciled_book_balance),
        "reconciliation_run_id": str(run_id),
        "run_command_hash": run_command_hash,
        "statement_closing_balance": str(bridge.statement_closing_balance),
        "statement_opening_balance": str(bridge.statement_opening_balance),
        "statement_period_movements": str(bridge.statement_period_movements),
        "statement_population_reference": bridge.statement_population_reference,
        "unexplained_difference": str(bridge.unexplained_difference),
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(
        ("reconciliation_run_transition_snapshot:v1|" + serialized).encode("utf-8")
    ).hexdigest()


def _load_transition_document(
    connection: object,
    tenant_id: UUID,
    tenant_reference: str,
    run_id: UUID,
    idempotency_key: str,
    *,
    replayed: bool,
) -> dict[str, object]:
    """Return one persisted lifecycle receipt with immutable command evidence."""
    row = connection.execute(
        """
        SELECT transition.reconciliation_run_transition_command_id,
               transition.reconciliation_snapshot_hash,
               transition.reconciliation_transition_command_hash,
               transition.actor_reference, transition.purpose_code,
               transition.effective_at, transition.recorded_at, run.run_status_code,
               transition.statement_population_reference,
               transition.book_population_reference
        FROM accounting_core.reconciliation_run_transition_command AS transition
        JOIN accounting_core.reconciliation_run AS run
          ON run.tenant_account_id = transition.tenant_account_id
         AND run.reconciliation_run_id = transition.reconciliation_run_id
        WHERE transition.tenant_account_id = %s
          AND transition.reconciliation_run_id = %s
          AND transition.reconciliation_transition_idempotency_key = %s
        """,
        (tenant_id, run_id, idempotency_key),
    ).fetchone()
    if row is None:
        raise AccountingValidationError(
            "reconciliation lifecycle command evidence is missing. Restore the retained transition evidence, then retry."
        )
    return {
        "tenant_reference": tenant_reference,
        "reconciliation_run_id": str(run_id),
        "run_status_code": row[7],
        "reconciliation_transition_id": str(row[0]),
        "reconciliation_idempotency_key": idempotency_key,
        "reconciliation_snapshot_hash": row[1],
        "reconciliation_transition_command_hash": row[2],
        "actor_reference": row[3],
        "purpose_code": row[4],
        "effective_at": _format_timestamp(row[5]),
        "recorded_at": _format_timestamp(row[6]),
        "statement_population_reference": row[8],
        "book_population_reference": row[9],
        "next_action": _RECONCILED_NEXT_ACTION,
        "replayed": replayed,
    }


__all__ = ["reconcile_reconciliation_run"]
