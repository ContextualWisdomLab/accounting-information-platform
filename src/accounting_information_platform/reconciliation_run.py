"""Open and read immutable tenant-scoped reconciliation-run evidence."""

from __future__ import annotations

import hashlib
import json
import re
from functools import wraps
from datetime import datetime, timezone
from typing import Callable, Mapping, ParamSpec
from uuid import UUID

import psycopg

from .core import (
    AccountingValidationError,
    IdempotencyConflictError,
    _HASH_PATTERN,
    _require_reference,
)
from .persistence import PostgresPostingLedger, _format_timestamp

_RUN_NEXT_ACTION = (
    "Run deterministic matching and review candidates before producing close evidence."
)
_CANONICAL_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
)
_P = ParamSpec("_P")


def _normalize_reconciliation_command_identity_conflicts(
    command: Callable[_P, dict[str, object]],
) -> Callable[_P, dict[str, object]]:
    """Translate only the database-owned shared command identity conflict."""

    @wraps(command)
    def normalized(*args: _P.args, **kwargs: _P.kwargs) -> dict[str, object]:
        try:
            return command(*args, **kwargs)
        except psycopg.errors.UniqueViolation as error:
            message_primary = getattr(error.diag, "message_primary", "") or ""
            if (
                error.sqlstate != "23505"
                or "reconciliation_command_identity_conflict" not in message_primary
            ):
                raise
            raise IdempotencyConflictError(
                "reconciliation idempotency key was concurrently claimed by another command. "
                "Supply a new reconciliation_idempotency_key, then retry."
            ) from error

    return normalized


@_normalize_reconciliation_command_identity_conflicts
def accept_reconciliation_run(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Open one evaluating run from an immutable statement and book assignment."""
    command = _require_command(payload, tenant_reference)
    statement_id = _parse_uuid(
        str(command.get("bank_statement_record_id") or ""),
        "bank_statement_record_id",
    )
    legal_entity_reference = str(command.get("legal_entity_reference") or "")
    accounting_book_reference = str(command.get("accounting_book_reference") or "")
    _require_reference(legal_entity_reference, "legal entity reference")
    _require_reference(accounting_book_reference, "accounting book reference")
    raw_matching_policy_version = command.get("matching_policy_version")
    if (
        not isinstance(raw_matching_policy_version, str)
        or not raw_matching_policy_version
        or raw_matching_policy_version != raw_matching_policy_version.strip()
    ):
        raise AccountingValidationError(
            "matching_policy_version is required and must be a canonical string. "
            "Supply the deterministic matching policy version, then retry the run."
        )
    matching_policy_version = raw_matching_policy_version
    idempotency_key = command.get("reconciliation_idempotency_key")
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key
        or idempotency_key != idempotency_key.strip()
    ):
        raise AccountingValidationError(
            "reconciliation_idempotency_key is required and must be a canonical non-empty string. "
            "Supply the reconciliation run command key, then retry the run."
        )
    source_payload_hash = command.get("source_payload_hash")
    if not isinstance(source_payload_hash, str) or _HASH_PATTERN.fullmatch(
        source_payload_hash
    ) is None:
        raise AccountingValidationError(
            "source_payload_hash must be a canonical sha256 digest. "
            "Supply the immutable bank-statement source hash, then retry the run."
        )
    bank_cutoff_at = _parse_timestamp(
        str(command.get("bank_cutoff_at") or ""), "bank_cutoff_at"
    )
    book_cutoff_at = _parse_timestamp(
        str(command.get("book_cutoff_at") or ""), "book_cutoff_at"
    )
    knowledge_cutoff_at = _parse_timestamp(
        str(command.get("knowledge_cutoff_at") or ""), "knowledge_cutoff_at"
    )
    if bank_cutoff_at > knowledge_cutoff_at or book_cutoff_at > knowledge_cutoff_at:
        raise AccountingValidationError(
            "bank_cutoff_at and book_cutoff_at must not be after knowledge_cutoff_at. "
            "Supply cutoffs within the run knowledge boundary, then retry the run."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(
            connection, f"reconciliation_run_key:{idempotency_key}"
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
        if prior_identity is not None and prior_identity[0] != "run_opening":
            raise IdempotencyConflictError(
                "reconciliation idempotency key is already owned by a lifecycle command. "
                "Supply a new reconciliation_idempotency_key, then retry the run."
            )
        prior_command = connection.execute(
            """
            SELECT reconciliation_run_id, source_payload_hash,
                   bank_statement_record_id
            FROM accounting_core.reconciliation_run_command
            WHERE tenant_account_id = %s
              AND reconciliation_idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior_command is not None:
            prior_document = _load_reconciliation_run_document(
                connection,
                tenant_id,
                tenant_reference,
                prior_command[0],
                replayed=True,
            )
            if (
                prior_command[1] != source_payload_hash
                or prior_command[2] != statement_id
                or prior_document["legal_entity_reference"] != legal_entity_reference
                or prior_document["accounting_book_reference"] != accounting_book_reference
                or prior_document["bank_cutoff_at"] != _format_timestamp(bank_cutoff_at)
                or prior_document["book_cutoff_at"] != _format_timestamp(book_cutoff_at)
                or prior_document["matching_policy_version"] != matching_policy_version
                or prior_document["knowledge_cutoff_at"]
                != _format_timestamp(knowledge_cutoff_at)
            ):
                raise IdempotencyConflictError(
                    "reconciliation idempotency key was already used with different run evidence. "
                    "Supply a new reconciliation_idempotency_key, then retry the run."
                )
            return prior_document
        binding_rows = connection.execute(
            """
            SELECT assignment.bank_account_assignment_id,
                   legal_entity.legal_entity_code,
                   accounting_book.book_name,
                   account.bank_account_reference,
                   account.account_currency_code,
                   statement.statement_identity_reference,
                   statement.period_start_at,
                   statement.period_end_at,
                   statement.source_artifact_hash,
                   statement.normalized_payload_hash,
                   artifact.artifact_store_reference
            FROM accounting_integration.bank_statement_record AS statement
            JOIN accounting_core.bank_account_record AS account
              ON account.tenant_account_id = statement.tenant_account_id
             AND account.bank_account_record_id = statement.bank_account_record_id
            JOIN accounting_core.bank_account_assignment AS assignment
              ON assignment.tenant_account_id = account.tenant_account_id
             AND assignment.bank_account_record_id = account.bank_account_record_id
            JOIN accounting_core.legal_entity_record AS legal_entity
              ON legal_entity.tenant_account_id = assignment.tenant_account_id
             AND legal_entity.legal_entity_id = assignment.legal_entity_id
            JOIN accounting_core.accounting_book AS accounting_book
              ON accounting_book.tenant_account_id = assignment.tenant_account_id
             AND accounting_book.accounting_book_id = assignment.accounting_book_id
            JOIN accounting_integration.bank_statement_artifact AS artifact
              ON artifact.tenant_account_id = statement.tenant_account_id
             AND artifact.bank_statement_artifact_id = statement.bank_statement_artifact_id
            WHERE statement.tenant_account_id = %s
              AND statement.bank_statement_record_id = %s
              AND legal_entity.legal_entity_code = %s
              AND accounting_book.book_name = %s
              AND assignment.valid_from <= %s
              AND (assignment.valid_to IS NULL OR assignment.valid_to > %s)
              AND statement.recorded_at <= %s
              AND account.recorded_at <= %s
              AND assignment.recorded_at <= %s
              AND legal_entity.recorded_at <= %s
              AND accounting_book.recorded_at <= %s
              AND artifact.recorded_at <= %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM accounting_integration.bank_statement_balance AS balance
                  WHERE balance.tenant_account_id = statement.tenant_account_id
                    AND balance.bank_statement_record_id = statement.bank_statement_record_id
                    AND balance.recorded_at > %s
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM accounting_integration.bank_statement_entry AS entry
                  WHERE entry.tenant_account_id = statement.tenant_account_id
                    AND entry.bank_statement_record_id = statement.bank_statement_record_id
                    AND entry.recorded_at > %s
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM accounting_integration.bank_statement_entry_detail AS detail
                  JOIN accounting_integration.bank_statement_entry AS entry
                    ON entry.tenant_account_id = detail.tenant_account_id
                   AND entry.bank_statement_entry_id = detail.bank_statement_entry_id
                  WHERE entry.tenant_account_id = statement.tenant_account_id
                    AND entry.bank_statement_record_id = statement.bank_statement_record_id
                    AND detail.recorded_at > %s
              )
            FOR SHARE OF assignment
            """,
            (
                tenant_id,
                statement_id,
                legal_entity_reference,
                accounting_book_reference,
                bank_cutoff_at,
                bank_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
                knowledge_cutoff_at,
            ),
        ).fetchall()
        if not binding_rows:
            raise AccountingValidationError(
                "bank statement is not bound to the requested legal entity, accounting book, "
                "and cutoff. Select the statement's active bank assignment, then retry the run."
            )
        if len(binding_rows) != 1:
            raise AccountingValidationError(
                "bank statement has more than one active accounting assignment at the cutoff. "
                "Resolve the assignment overlap, then retry the run."
            )
        binding = binding_rows[0]
        if binding[6] is not None and binding[6] > bank_cutoff_at:
            raise AccountingValidationError(
                "bank_cutoff_at is before the statement period. Supply a cutoff covering the statement, then retry the run."
            )
        if binding[7] is not None and binding[7] > bank_cutoff_at:
            raise AccountingValidationError(
                "bank_cutoff_at is before the statement period end. Supply a cutoff covering the statement, then retry the run."
            )
        if source_payload_hash != binding[8]:
            raise AccountingValidationError(
                "source_payload_hash does not match the persisted bank statement. "
                "Supply the statement's immutable source hash, then retry the run."
            )
        command_hash = _command_hash(
            tenant_reference=tenant_reference,
            statement_id=statement_id,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            bank_cutoff_at=bank_cutoff_at,
            book_cutoff_at=book_cutoff_at,
            matching_policy_version=matching_policy_version,
            knowledge_cutoff_at=knowledge_cutoff_at,
            idempotency_key=idempotency_key,
            source_payload_hash=source_payload_hash,
            assignment_id=binding[0],
            normalized_payload_hash=binding[9],
        )
        run_id = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run (
                tenant_account_id, legal_entity_id, accounting_book_id,
                bank_account_assignment_id, currency_code, bank_cutoff_at,
                book_cutoff_at, matching_policy_version, knowledge_cutoff_at,
                run_status_code
            )
            SELECT %s, assignment.legal_entity_id, assignment.accounting_book_id,
                   assignment.bank_account_assignment_id, account.account_currency_code,
                   %s, %s, %s, %s, 'evaluating'
            FROM accounting_core.bank_account_assignment AS assignment
            JOIN accounting_core.bank_account_record AS account
              ON account.tenant_account_id = assignment.tenant_account_id
             AND account.bank_account_record_id = assignment.bank_account_record_id
            WHERE assignment.tenant_account_id = %s
              AND assignment.bank_account_assignment_id = %s
            RETURNING reconciliation_run_id
            """,
            (
                tenant_id,
                bank_cutoff_at,
                book_cutoff_at,
                matching_policy_version,
                knowledge_cutoff_at,
                tenant_id,
                binding[0],
            ),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run_command (
                tenant_account_id, reconciliation_run_id, bank_statement_record_id,
                reconciliation_idempotency_key, reconciliation_command_hash,
                source_payload_hash, source_payload_reference
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                run_id,
                statement_id,
                idempotency_key,
                command_hash,
                source_payload_hash,
                binding[10],
            ),
        )
        connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_evidence (
                tenant_account_id, reconciliation_run_id, evidence_type_code,
                evidence_reference, evidence_payload_hash, effective_at
            )
            VALUES (%s, %s, 'bank_statement', %s, %s, %s)
            """,
            (tenant_id, run_id, str(statement_id), source_payload_hash, bank_cutoff_at),
        )
        return _load_reconciliation_run_document(
            connection,
            tenant_id,
            tenant_reference,
            run_id,
            replayed=False,
        )


def lookup_reconciliation_run(
    database_url: str, tenant_reference: str, reconciliation_run_id: str
) -> dict[str, object]:
    """Read one persisted reconciliation run and its immutable statement binding."""
    run_id = _parse_uuid(reconciliation_run_id, "reconciliation_run_id")
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        return _load_reconciliation_run_document(
            connection,
            tenant_id,
            tenant_reference,
            run_id,
            replayed=False,
        )


def _load_reconciliation_run_document(
    connection: object,
    tenant_id: UUID,
    tenant_reference: str,
    run_id: UUID,
    *,
    replayed: bool,
) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT run.reconciliation_run_id,
               legal_entity.legal_entity_code,
               accounting_book.book_name,
               account.bank_account_reference,
               assignment.bank_account_assignment_id,
               run.currency_code,
               run.bank_cutoff_at,
               run.book_cutoff_at,
               run.matching_policy_version,
               run.knowledge_cutoff_at,
               run.run_status_code,
               command.bank_statement_record_id,
               statement.statement_identity_reference,
               command.reconciliation_idempotency_key,
               command.reconciliation_command_hash,
               command.source_payload_hash,
               command.source_payload_reference,
               statement.source_artifact_hash,
               statement.normalized_payload_hash
        FROM accounting_core.reconciliation_run AS run
        JOIN accounting_core.reconciliation_run_command AS command
          ON command.tenant_account_id = run.tenant_account_id
         AND command.reconciliation_run_id = run.reconciliation_run_id
        JOIN accounting_integration.bank_statement_record AS statement
          ON statement.tenant_account_id = command.tenant_account_id
         AND statement.bank_statement_record_id = command.bank_statement_record_id
        JOIN accounting_core.bank_account_assignment AS assignment
          ON assignment.tenant_account_id = run.tenant_account_id
         AND assignment.bank_account_assignment_id = run.bank_account_assignment_id
        JOIN accounting_core.bank_account_record AS account
          ON account.tenant_account_id = assignment.tenant_account_id
         AND account.bank_account_record_id = assignment.bank_account_record_id
        JOIN accounting_core.legal_entity_record AS legal_entity
          ON legal_entity.tenant_account_id = run.tenant_account_id
         AND legal_entity.legal_entity_id = run.legal_entity_id
        JOIN accounting_core.accounting_book AS accounting_book
          ON accounting_book.tenant_account_id = run.tenant_account_id
         AND accounting_book.accounting_book_id = run.accounting_book_id
        WHERE run.tenant_account_id = %s
          AND run.reconciliation_run_id = %s
        """,
        (tenant_id, run_id),
    ).fetchone()
    if row is None:
        raise AccountingValidationError(
            "reconciliation run is not recorded for this tenant. "
            "Supply a persisted reconciliation_run_id, then retry the run read."
        )
    return {
        "tenant_reference": tenant_reference,
        "reconciliation_run_id": str(row[0]),
        "legal_entity_reference": row[1],
        "accounting_book_reference": row[2],
        "bank_account_reference": row[3],
        "bank_account_assignment_id": str(row[4]),
        "currency_code": row[5],
        "bank_cutoff_at": _format_timestamp(row[6]),
        "book_cutoff_at": _format_timestamp(row[7]),
        "matching_policy_version": row[8],
        "knowledge_cutoff_at": _format_timestamp(row[9]),
        "run_status_code": row[10],
        "bank_statement_record_id": str(row[11]),
        "statement_identity_reference": row[12],
        "reconciliation_idempotency_key": row[13],
        "reconciliation_command_hash": row[14],
        "source_payload_hash": row[15],
        "source_payload_reference": row[16],
        "statement_source_artifact_hash": row[17],
        "statement_normalized_payload_hash": row[18],
        "next_action": _RUN_NEXT_ACTION,
        "replayed": replayed,
    }


def _require_command(payload: object, tenant_reference: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "reconciliation run payload must be a JSON object. "
            "Supply a reconciliation run command, then retry the run."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "reconciliation run tenant_reference does not match the bound tenant. "
            "Send the command to that tenant's AIS endpoint, then retry the run."
        )
    return payload


def _parse_uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError) as error:
        raise AccountingValidationError(
            f"{label} must be a UUID. Supply a persisted {label}, then retry the run."
        ) from error


def _parse_timestamp(value: str, label: str) -> datetime:
    if _CANONICAL_UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError(
            f"{label} must be a canonical UTC timestamp with an explicit UTC "
            "offset, using T and Z or +00:00. Supply an unambiguous UTC "
            "timestamp, then retry the run."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingValidationError(
            f"{label} must be an ISO-8601 timestamp. Supply a UTC timestamp, then retry the run."
        ) from error
    return parsed.astimezone(timezone.utc)


def _command_hash(
    *,
    tenant_reference: str,
    statement_id: UUID,
    legal_entity_reference: str,
    accounting_book_reference: str,
    bank_cutoff_at: datetime,
    book_cutoff_at: datetime,
    matching_policy_version: str,
    knowledge_cutoff_at: datetime,
    idempotency_key: str,
    source_payload_hash: str,
    assignment_id: UUID,
    normalized_payload_hash: str,
) -> str:
    """Return the canonical hash for one run command and its bound evidence."""
    payload = {
        "accounting_book_reference": accounting_book_reference,
        "bank_account_assignment_id": str(assignment_id),
        "bank_cutoff_at": _format_timestamp(bank_cutoff_at),
        "bank_statement_record_id": str(statement_id),
        "book_cutoff_at": _format_timestamp(book_cutoff_at),
        "knowledge_cutoff_at": _format_timestamp(knowledge_cutoff_at),
        "legal_entity_reference": legal_entity_reference,
        "matching_policy_version": matching_policy_version,
        "normalized_payload_hash": normalized_payload_hash,
        "reconciliation_idempotency_key": idempotency_key,
        "source_payload_hash": source_payload_hash,
        "tenant_reference": tenant_reference,
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()