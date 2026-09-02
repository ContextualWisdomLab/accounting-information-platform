"""Resolve one reconciliation exception through immutable maker-checker evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping
from uuid import UUID

from .core import (
    AccountingValidationError,
    IdempotencyConflictError,
    _HASH_PATTERN,
    _require_code,
    _require_reference,
)
from .persistence import PostgresPostingLedger, _format_timestamp
from .reconciliation_run import (
    _normalize_reconciliation_command_identity_conflicts,
    _parse_timestamp,
    _parse_uuid,
)

_RESOLUTION_HASH_SENTINEL = "sha256:" + "0" * 64
_RESOLUTION_NEXT_ACTION = (
    "Review the remaining reconciliation exceptions and reviewed matches; when all "
    "controls and the exact book-to-bank bridge tie, execute the run reconciliation command."
)
_SERIALIZATION_FAILURE_SQLSTATE = "40001"
_SERIALIZATION_ATTEMPTS = 3


@_normalize_reconciliation_command_identity_conflicts
def resolve_reconciliation_exception(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Resolve or supersede one open exception with durable maker-checker evidence."""
    command = _require_resolution_command(payload, tenant_reference)
    source_payload_hash = _source_payload_hash(command)
    run_id = _parse_uuid(
        str(command.get("reconciliation_run_id") or ""), "reconciliation_run_id"
    )
    exception_id = _parse_uuid(
        str(command.get("reconciliation_exception_id") or ""),
        "reconciliation_exception_id",
    )
    idempotency_key = _canonical_text(
        command.get("reconciliation_idempotency_key"),
        "reconciliation_idempotency_key",
    )
    target_status = str(command.get("resolution_status_code") or "")
    if target_status not in {"resolved", "superseded"}:
        raise AccountingValidationError(
            "resolution_status_code must be resolved or superseded. Supply the reviewed "
            "terminal decision, then retry the exception resolution."
        )
    actor_reference = _canonical_text(command.get("actor_reference"), "actor_reference")
    purpose_code = _canonical_text(command.get("purpose_code"), "purpose_code")
    evidence_reference = _canonical_text(
        command.get("resolution_evidence_reference"), "resolution_evidence_reference"
    )
    evidence_hash = command.get("resolution_evidence_hash")
    if not isinstance(evidence_hash, str) or _HASH_PATTERN.fullmatch(evidence_hash) is None:
        raise AccountingValidationError(
            "resolution_evidence_hash must be a canonical sha256 digest. Retain the reviewed "
            "resolution evidence, supply its exact digest, then retry."
        )
    _require_reference(actor_reference, "actor reference")
    _require_reference(evidence_reference, "resolution evidence reference")
    _require_code(purpose_code, "purpose code")
    effective_at = _parse_timestamp(str(command.get("effective_at") or ""), "effective_at")

    for attempt in range(_SERIALIZATION_ATTEMPTS):
        try:
            return _resolve_reconciliation_exception_once(
                database_url=database_url,
                tenant_reference=tenant_reference,
                run_id=run_id,
                exception_id=exception_id,
                idempotency_key=idempotency_key,
                target_status=target_status,
                evidence_reference=evidence_reference,
                evidence_hash=evidence_hash,
                source_payload_hash=source_payload_hash,
                actor_reference=actor_reference,
                purpose_code=purpose_code,
                effective_at=effective_at,
            )
        except Exception as error:
            if (
                getattr(error, "sqlstate", None) != _SERIALIZATION_FAILURE_SQLSTATE
                or attempt + 1 >= _SERIALIZATION_ATTEMPTS
            ):
                raise
    raise AssertionError("serialization retry loop exhausted without returning or raising")


def _resolve_reconciliation_exception_once(
    *,
    database_url: str,
    tenant_reference: str,
    run_id: UUID,
    exception_id: UUID,
    idempotency_key: str,
    target_status: str,
    evidence_reference: str,
    evidence_hash: str,
    source_payload_hash: str,
    actor_reference: str,
    purpose_code: str,
    effective_at: object,
) -> dict[str, object]:
    """Execute one repeatable-read resolution attempt; 40001 retries start fresh."""
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        ledger._acquire_command_lock(connection, f"reconciliation_run_lifecycle:{run_id}")
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(
            connection, f"reconciliation_exception_resolution_key:{idempotency_key}"
        )

        prior = connection.execute(
            """
            SELECT reconciliation_run_id,
                   reconciliation_exception_id,
                   target_resolution_status_code,
                   resolution_evidence_reference,
                   resolution_evidence_hash,
                   source_payload_hash,
                   actor_reference,
                   purpose_code,
                   effective_at
            FROM accounting_core.reconciliation_exception_resolution_command
            WHERE tenant_account_id = %s
              AND reconciliation_resolution_idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior is not None:
            expected = (
                run_id,
                exception_id,
                target_status,
                evidence_reference,
                evidence_hash,
                source_payload_hash,
                actor_reference,
                purpose_code,
                effective_at,
            )
            if prior != expected:
                raise IdempotencyConflictError(
                    "reconciliation exception resolution idempotency key was already used with "
                    "different evidence or source payload. Supply a new "
                    "reconciliation_idempotency_key, then retry."
                )
            return _load_resolution_document(
                connection,
                tenant_id,
                tenant_reference,
                run_id,
                exception_id,
                idempotency_key,
                replayed=True,
            )

        prior_identity = connection.execute(
            """
            SELECT command_family_code
            FROM accounting_core.reconciliation_command_identity
            WHERE tenant_account_id = %s
              AND reconciliation_command_identity_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior_identity is not None:
            raise IdempotencyConflictError(
                "reconciliation idempotency key is already owned by another reconciliation "
                "command. Supply a new reconciliation_idempotency_key, then retry."
            )

        run_row = connection.execute(
            """
            SELECT run_status_code
            FROM accounting_core.reconciliation_run
            WHERE tenant_account_id = %s AND reconciliation_run_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id),
        ).fetchone()
        if run_row is None:
            raise AccountingValidationError(
                "reconciliation run is not recorded for this tenant. Supply the persisted "
                "reconciliation_run_id, then retry the exception resolution."
            )
        if str(run_row[0]) not in {"evaluating", "review_required"}:
            raise AccountingValidationError(
                f"reconciliation run is {run_row[0]}; only evaluating or review_required runs "
                "permit exception resolution. Use the original evidence or create a new run."
            )

        exception_row = connection.execute(
            """
            SELECT resolution_status_code, owner_reference, effective_at
            FROM accounting_core.reconciliation_exception
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
              AND reconciliation_exception_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id, exception_id),
        ).fetchone()
        if exception_row is None:
            raise AccountingValidationError(
                "reconciliation exception is not recorded in this tenant/run. Supply the owning "
                "reconciliation_exception_id, then retry."
            )
        if str(exception_row[0]) != "open":
            raise AccountingValidationError(
                "reconciliation exception is already terminal without this command key. Replay "
                "the original resolution idempotency key instead of rewriting terminal evidence."
            )
        if actor_reference == str(exception_row[1]):
            raise AccountingValidationError(
                "exception owner cannot approve the same exception resolution. Use a distinct "
                "authorized reviewer, then retry."
            )
        if effective_at < exception_row[2]:
            raise AccountingValidationError(
                "exception resolution effective_at cannot precede the exception effective time. "
                "Supply the actual review time, then retry."
            )

        resolution_id, resolution_hash, _recorded_at = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_exception_resolution_command (
                tenant_account_id,
                reconciliation_run_id,
                reconciliation_exception_id,
                reconciliation_resolution_idempotency_key,
                target_resolution_status_code,
                resolution_evidence_reference,
                resolution_evidence_hash,
                source_payload_hash,
                reconciliation_exception_resolution_command_hash,
                actor_reference,
                purpose_code,
                effective_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING reconciliation_exception_resolution_command_id,
                      reconciliation_exception_resolution_command_hash,
                      recorded_at
            """,
            (
                tenant_id,
                run_id,
                exception_id,
                idempotency_key,
                target_status,
                evidence_reference,
                evidence_hash,
                source_payload_hash,
                _RESOLUTION_HASH_SENTINEL,
                actor_reference,
                purpose_code,
                effective_at,
            ),
        ).fetchone()
        if resolution_hash == _RESOLUTION_HASH_SENTINEL:
            raise AccountingValidationError(
                "reconciliation exception resolution command hash was not assigned by the "
                "database. Restore the resolution trigger, verify the migration, then retry."
            )

        connection.execute(
            """
            UPDATE accounting_core.reconciliation_exception
            SET resolution_status_code = %s
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
              AND reconciliation_exception_id = %s
            """,
            (target_status, tenant_id, run_id, exception_id),
        )
        event_type = (
            "reconciliation_exception_resolved"
            if target_status == "resolved"
            else "reconciliation_exception_superseded"
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
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                event_type,
                f"urn:cwl:accounting:reconciliation_exception:{exception_id}",
                "urn:cwl:accounting:reconciliation_exception_resolution:"
                + str(resolution_id),
                resolution_hash,
            ),
        )
        return _load_resolution_document(
            connection,
            tenant_id,
            tenant_reference,
            run_id,
            exception_id,
            idempotency_key,
            replayed=False,
        )


def _require_resolution_command(
    payload: object, tenant_reference: str
) -> Mapping[str, object]:
    """Return one exception-resolution command bound to the endpoint tenant."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "reconciliation exception resolution payload must be a JSON object. Supply the "
            "reviewed resolution command, then retry."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "reconciliation exception resolution tenant_reference does not match the bound "
            "tenant. Send the command to that tenant's AIS endpoint, then retry."
        )
    if payload.get("reconciliation_action_code") != "resolve_exception":
        raise AccountingValidationError(
            "reconciliation_action_code must be resolve_exception. Supply the supported "
            "exception-resolution action, then retry."
        )
    return payload


def _source_payload_hash(command: Mapping[str, object]) -> str:
    """Hash the complete strict-JSON command so idempotency binds every received member."""
    try:
        canonical = json.dumps(
            command,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise AccountingValidationError(
            "reconciliation exception resolution payload must contain JSON-compatible values. "
            "Supply the exact JSON command, then retry."
        ) from error
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_text(value: object, field_name: str) -> str:
    """Require a non-empty canonical string without surrounding whitespace."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise AccountingValidationError(
            f"{field_name} is required and must be a canonical non-empty string. "
            f"Supply a valid {field_name}, then retry."
        )
    return value


def _load_resolution_document(
    connection: object,
    tenant_id: UUID,
    tenant_reference: str,
    run_id: UUID,
    exception_id: UUID,
    idempotency_key: str,
    *,
    replayed: bool,
) -> dict[str, object]:
    """Return the immutable resolution receipt rather than rebuilding later state."""
    row = connection.execute(
        """
        SELECT command.reconciliation_exception_resolution_command_id,
               command.target_resolution_status_code,
               command.resolution_evidence_reference,
               command.resolution_evidence_hash,
               command.source_payload_hash,
               command.reconciliation_exception_resolution_command_hash,
               command.actor_reference,
               command.purpose_code,
               command.effective_at,
               command.recorded_at
        FROM accounting_core.reconciliation_exception_resolution_command AS command
        WHERE command.tenant_account_id = %s
          AND command.reconciliation_run_id = %s
          AND command.reconciliation_exception_id = %s
          AND command.reconciliation_resolution_idempotency_key = %s
        """,
        (tenant_id, run_id, exception_id, idempotency_key),
    ).fetchone()
    if row is None:
        raise AccountingValidationError(
            "reconciliation exception resolution command evidence is missing. Restore the "
            "retained command evidence through an audited migration, then retry."
        )
    return {
        "tenant_reference": tenant_reference,
        "reconciliation_run_id": str(run_id),
        "reconciliation_exception_id": str(exception_id),
        "resolution_status_code": row[1],
        "reconciliation_exception_resolution_id": str(row[0]),
        "reconciliation_idempotency_key": idempotency_key,
        "resolution_evidence_reference": row[2],
        "resolution_evidence_hash": row[3],
        "source_payload_hash": row[4],
        "reconciliation_exception_resolution_command_hash": row[5],
        "actor_reference": row[6],
        "purpose_code": row[7],
        "effective_at": _format_timestamp(row[8]),
        "recorded_at": _format_timestamp(row[9]),
        "next_action": _RESOLUTION_NEXT_ACTION,
        "replayed": replayed,
    }


__all__ = ["resolve_reconciliation_exception"]
