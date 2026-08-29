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


def accept_reconciliation_match(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Persist one exact 1:1 proposed match for an evaluating reconciliation run."""
    command = _require_command(payload, tenant_reference)
    run_id = _parse_match_uuid(
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
            SELECT run_status_code, accounting_book_id, currency_code,
                   bank_account_assignment_id, bank_cutoff_at, book_cutoff_at,
                   knowledge_cutoff_at
            FROM accounting_core.reconciliation_run
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
            FOR UPDATE
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
        _require_recorded_source_amounts(
            connection,
            tenant_id=tenant_id,
            reconciliation_run_id=run_id,
            accounting_book_id=run[1],
            currency_code=run[2],
            bank_account_assignment_id=run[3],
            bank_cutoff_at=run[4],
            book_cutoff_at=run[5],
            knowledge_cutoff_at=run[6],
            statement_reference=statement_reference,
            journal_reference=journal_reference,
            statement_amount=statement_amount,
            journal_amount=journal_amount,
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

        from psycopg.errors import CheckViolation

        try:
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
        except CheckViolation as error:
            raise AccountingValidationError(
                "the proposed match conflicts with recorded source conservation evidence. "
                "Refresh the source amounts and evaluating run, then retry the match."
            ) from error
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
    match_id = _parse_match_uuid(reconciliation_match_id, "reconciliation_match_id")
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


def _parse_match_uuid(value: str, label: str) -> UUID:
    """Parse a match command identifier with match-specific recovery guidance."""
    try:
        return UUID(value)
    except (ValueError, AttributeError) as error:
        raise AccountingValidationError(
            f"{label} must be a UUID. Supply a persisted {label}, then retry the match."
        ) from error


def _require_recorded_source_amounts(
    connection: object,
    *,
    tenant_id: UUID,
    reconciliation_run_id: UUID,
    accounting_book_id: UUID,
    currency_code: str,
    bank_account_assignment_id: UUID,
    bank_cutoff_at: object,
    book_cutoff_at: object,
    knowledge_cutoff_at: object,
    statement_reference: str,
    journal_reference: str,
    statement_amount: Decimal,
    journal_amount: Decimal,
) -> None:
    """Require exact source amounts, direction, assignment, and run cutoffs."""
    statement_rows = connection.execute(
        """
        SELECT entry.entry_amount, entry.entry_currency_code,
               entry.credit_debit_code
        FROM accounting_integration.bank_statement_entry AS entry
        JOIN accounting_core.reconciliation_run_command AS run_command
          ON run_command.tenant_account_id = entry.tenant_account_id
         AND run_command.bank_statement_record_id = entry.bank_statement_record_id
        JOIN accounting_core.reconciliation_run AS run_scope
          ON run_scope.tenant_account_id = run_command.tenant_account_id
         AND run_scope.reconciliation_run_id = run_command.reconciliation_run_id
        WHERE run_command.tenant_account_id = %s
          AND run_command.reconciliation_run_id = %s
          AND (entry.source_entry_identity = %s OR entry.bank_statement_entry_id::text = %s)
          AND run_scope.currency_code = entry.entry_currency_code
          AND (entry.booking_occurred_at IS NULL OR entry.booking_occurred_at <= %s)
          AND (entry.value_occurred_at IS NULL OR entry.value_occurred_at <= %s)
          AND entry.recorded_at <= %s
        """,
        (
            tenant_id,
            reconciliation_run_id,
            statement_reference,
            statement_reference,
            bank_cutoff_at,
            bank_cutoff_at,
            knowledge_cutoff_at,
        ),
    ).fetchall()
    if len(statement_rows) != 1:
        raise AccountingValidationError(
            "statement source evidence is not recorded exactly once for this reconciliation run. "
            "Supply an entry reference from the bound bank statement, then retry the match."
        )
    recorded_statement_amount = statement_rows[0][0]
    if recorded_statement_amount != statement_amount:
        raise AccountingValidationError(
            "statement_amount does not match recorded statement source amount. "
            "Supply the exact recorded statement amount, then retry the match."
        )
    statement_direction = statement_rows[0][2]
    if statement_direction not in {"CRDT", "DBIT"}:
        raise AccountingValidationError(
            "statement source evidence has an unsupported direction. "
            "Supply a CRDT or DBIT statement entry, then retry the match."
        )

    journal_row = connection.execute(
        """
        SELECT journal.journal_status_code,
               journal.transaction_currency_code,
               COALESCE(SUM(line.debit_amount), 0),
               COALESCE(SUM(line.credit_amount), 0),
               COALESCE(
                   SUM(
                       CASE
                           WHEN line.chart_account_id = assignment.chart_account_id
                           THEN line.debit_amount
                           ELSE 0
                       END
                   ),
                   0
               ),
               COALESCE(
                   SUM(
                       CASE
                           WHEN line.chart_account_id = assignment.chart_account_id
                           THEN line.credit_amount
                           ELSE 0
                       END
                   ),
                   0
               )
        FROM accounting_core.general_journal AS journal
        JOIN accounting_core.bank_account_assignment AS assignment
          ON assignment.tenant_account_id = journal.tenant_account_id
         AND assignment.accounting_book_id = journal.accounting_book_id
        LEFT JOIN accounting_core.journal_entry_line AS line
          ON line.tenant_account_id = journal.tenant_account_id
         AND line.general_journal_id = journal.general_journal_id
        WHERE journal.tenant_account_id = %s
          AND journal.accounting_book_id = %s
          AND journal.journal_reference = %s
          AND assignment.bank_account_assignment_id = %s
          AND journal.accounting_date <= (%s::timestamptz AT TIME ZONE 'UTC')::date
          AND journal.posted_at <= %s
        GROUP BY journal.general_journal_id, journal.journal_status_code,
                 journal.transaction_currency_code, assignment.chart_account_id
        """,
        (
            tenant_id,
            accounting_book_id,
            journal_reference,
            bank_account_assignment_id,
            book_cutoff_at,
            knowledge_cutoff_at,
        ),
    ).fetchone()
    if journal_row is None:
        raise AccountingValidationError(
            "journal source evidence is not recorded in the reconciliation scope. "
            "Supply a recorded journal reference from the bound accounting book, then retry the match."
        )
    if journal_row[0:2] != ("posted", currency_code):
        raise AccountingValidationError(
            "journal source evidence is not a posted journal in the reconciliation scope. "
            "Supply a posted journal reference from the bound accounting book, then retry the match."
        )
    if journal_row[2] != journal_row[3] or journal_row[2] <= 0:
        raise AccountingValidationError(
            "journal source evidence is not balanced and positive. "
            "Supply a balanced posted journal, then retry the match."
        )
    cash_debit = journal_row[4]
    cash_credit = journal_row[5]
    expected_cash_debit = statement_direction == "CRDT"
    if (
        expected_cash_debit
        and cash_credit != 0
    ) or (
        not expected_cash_debit
        and cash_debit != 0
    ):
        expected_side = "debit" if expected_cash_debit else "credit"
        raise AccountingValidationError(
            "journal source evidence direction does not match the statement direction; "
            f"the assigned cash line must carry the amount on the {expected_side} side. "
            "Supply matching CRDT/DBIT source evidence, then retry the match."
        )
    if (cash_debit if expected_cash_debit else cash_credit) != journal_amount:
        raise AccountingValidationError(
            "journal_amount does not match recorded assigned cash line amount in the journal source. "
            "Supply the exact recorded journal amount, then retry the match."
        )


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
