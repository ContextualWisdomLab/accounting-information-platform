"""Immutable ISO 20022 camt.053.001.14 bank-statement evidence registry.

A statement entry is evidence only. This module never posts, reverses, approves,
or changes a journal. Raw statement bytes belong in a host-owned artifact store;
PostgreSQL retains hashes, locators, and normalized facts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Protocol
from uuid import UUID
from xml.parsers import expat

from .core import (
    AccountingValidationError,
    IdempotencyConflictError,
    _HASH_PATTERN,
    _parse_amount,
    _require_currency,
    _require_reference,
)
from .persistence import PostgresPostingLedger, _format_timestamp

CAMT053_MESSAGE_DEFINITION = "camt.053.001.14"
CAMT053_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.14"
MAX_STATEMENT_BYTES = 1_048_576
MAX_XML_DEPTH = 32
MAX_ELEMENT_COUNT = 20_000
MAX_ATTRIBUTE_COUNT = 20_000
MAX_TEXT_BYTES = 262_144
MAX_STATEMENT_COUNT = 1
MAX_ENTRY_COUNT = 500
MAX_REMITTANCE_CHARS = 256
_PAGE_DEFAULT = 50
_PAGE_MAXIMUM = 100
_ADAPTER_ROOT = Path(__file__).resolve().parent / "iso20022"
_MANIFEST_PATH = _ADAPTER_ROOT / "adapter_manifest.json"


class ArtifactStore(Protocol):
    """Host-owned immutable store for original bank-statement bytes."""

    def put_artifact(self, source_artifact_hash: str, payload: bytes) -> str:
        """Persist *payload* under *source_artifact_hash* and return a locator."""

    def get_artifact(self, artifact_store_reference: str) -> bytes:
        """Return previously stored bytes for *artifact_store_reference*."""


class MemoryArtifactStore:
    """In-process host evidence store keyed by canonical artifact hash."""

    def __init__(self) -> None:
        """Create an empty memory-backed artifact store."""
        self._artifacts: dict[str, bytes] = {}

    def put_artifact(self, source_artifact_hash: str, payload: bytes) -> str:
        """Retain *payload* by hash and return a memory locator."""
        existing = self._artifacts.get(source_artifact_hash)
        if existing is not None and existing != payload:
            raise AccountingValidationError(
                "artifact store already holds different bytes for this source hash. "
                "Supply the original statement bytes, then retry ingest."
            )
        self._artifacts[source_artifact_hash] = payload
        return f"memory:{source_artifact_hash}"

    def get_artifact(self, artifact_store_reference: str) -> bytes:
        """Return stored bytes for a memory locator."""
        if not artifact_store_reference.startswith("memory:"):
            raise AccountingValidationError(
                "artifact store reference is not a memory locator. "
                "Use the host evidence store for that locator, then retry the read."
            )
        payload = self._artifacts.get(artifact_store_reference.removeprefix("memory:"))
        if payload is None:
            raise AccountingValidationError(
                "statement artifact is not retained in the host evidence store. "
                "Restore the original artifact, then retry the read."
            )
        return payload


@dataclass(frozen=True, slots=True)
class NormalizedEntryDetail:
    """One transaction-detail record that can affect identity or amount conservation."""

    detail_sequence_number: int
    source_locator_path: str
    detail_amount: Decimal
    detail_currency_code: str
    credit_debit_code: str
    end_to_end_reference: str | None
    account_servicer_reference: str | None
    remittance_evidence_text: str | None
    source_detail_hash: str


@dataclass(frozen=True, slots=True)
class NormalizedStatementEntry:
    """One normalized bank-statement entry with source locator and exact amount."""

    source_entry_identity: str | None
    entry_sequence_number: int
    source_locator_path: str
    booking_occurred_at: datetime | None
    value_occurred_at: datetime | None
    entry_amount: Decimal
    entry_currency_code: str
    credit_debit_code: str
    reversal_indicator: bool
    bank_transaction_domain_code: str | None
    bank_transaction_family_code: str | None
    bank_transaction_subfamily_code: str | None
    end_to_end_reference: str | None
    account_servicer_reference: str | None
    mandate_reference: str | None
    cheque_reference: str | None
    remittance_evidence_text: str | None
    counterparty_evidence_hash: str | None
    source_entry_hash: str
    entry_details: tuple[NormalizedEntryDetail, ...]


@dataclass(frozen=True, slots=True)
class NormalizedStatementBalance:
    """One exact camt.053 balance fact with its source locator and hash."""

    balance_sequence_number: int
    balance_type_code: str | None
    balance_amount: Decimal
    balance_currency_code: str
    credit_debit_code: str
    source_locator_path: str
    source_balance_hash: str


@dataclass(frozen=True, slots=True)
class NormalizedBankStatement:
    """One accepted camt.053.001.14 statement after fail-closed parse."""

    message_definition_identifier: str
    statement_identity_reference: str
    electronic_sequence_number: str | None
    legal_sequence_number: str | None
    period_start_at: datetime | None
    period_end_at: datetime | None
    opening_balance_hash: str | None
    closing_balance_hash: str | None
    account_currency_code: str
    account_identifier_hash: str
    source_artifact_hash: str
    normalized_payload_hash: str
    balances: tuple[NormalizedStatementBalance, ...]
    entries: tuple[NormalizedStatementEntry, ...]


def load_adapter_manifest() -> dict[str, object]:
    """Return the integrity-pinned camt.053.001.14 adapter manifest after hash checks."""
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("message_definition_identifier") != CAMT053_MESSAGE_DEFINITION:
        raise AccountingValidationError(
            "adapter manifest message-definition identifier is not camt.053.001.14. "
            "Restore the pinned adapter evidence, then retry ingest."
        )
    package_root = Path(__file__).resolve().parent
    for artifact in manifest["artifacts"]:
        relative_path = str(artifact["local_package_path"])
        payload = (package_root / relative_path).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != artifact["sha256"] or len(payload) != int(artifact["byte_length"]):
            raise AccountingValidationError(
                "adapter evidence SHA-256 or byte length does not match the pinned manifest. "
                "Restore the vendored ISO 20022 adapter files, then retry ingest."
            )
    return manifest


def load_canonical_statement_fixture() -> bytes:
    """Return the pinned valid camt.053.001.14 fixture bytes."""
    load_adapter_manifest()
    return (_ADAPTER_ROOT / "fixtures" / "camt.053.001.14.valid.xml").read_bytes()


def parse_bank_statement_payload(
    payload: bytes,
    message_definition_identifier: str,
) -> NormalizedBankStatement:
    """Parse *payload* as camt.053.001.14 and return one normalized statement."""
    load_adapter_manifest()
    if message_definition_identifier != CAMT053_MESSAGE_DEFINITION:
        raise AccountingValidationError(
            "message-definition identifier is not the pinned camt.053.001.14 adapter. "
            "Send BankToCustomerStatementV14, then retry ingest."
        )
    if not payload:
        raise AccountingValidationError(
            "statement payload is empty. Supply the original camt.053.001.14 bytes, then retry ingest."
        )
    if len(payload) > MAX_STATEMENT_BYTES:
        raise AccountingValidationError(
            "statement payload exceeds the 1 MiB adapter bound. "
            "Split the statement file, then retry ingest."
        )
    document = _parse_bounded_xml(payload)
    _reject_revision_mismatch(document)
    profile = json.loads(
        (_ADAPTER_ROOT / "fixtures" / "camt.053.001.14.structural-profile.json").read_text(
            encoding="utf-8"
        )
    )
    _require_structural_paths(document, tuple(profile["required_paths"]))
    statements = _child_elements(document, "BkToCstmrStmt", "Stmt")
    if len(statements) != 1:
        raise AccountingValidationError(
            "this adapter accepts exactly one Stmt per command. "
            "Send one statement, then retry ingest."
        )
    return _normalize_statement(statements[0], payload)


def accept_bank_account_record(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Register one opaque bank account for *tenant_reference*."""
    command = _require_command(payload, "a bank-account command", tenant_reference)
    bank_account_reference = str(command.get("bank_account_reference") or "")
    _require_reference(bank_account_reference, "bank account reference")
    account_currency_code = str(command.get("account_currency_code") or "")
    _require_currency(account_currency_code)
    identifier_hash = _account_identifier_hash(command)
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(connection, f"bank_account:{bank_account_reference}")
        existing = connection.execute(
            """
            SELECT bank_account_record_id, account_currency_code, account_identifier_hash
            FROM accounting_core.bank_account_record
            WHERE tenant_account_id = %s AND bank_account_reference = %s
            """,
            (tenant_id, bank_account_reference),
        ).fetchone()
        if existing is not None:
            if existing[1] != account_currency_code or existing[2] != identifier_hash:
                raise IdempotencyConflictError(
                    "bank account reference was already used with different account evidence. "
                    "Supply a new bank_account_reference, then retry the account register"
                )
            return _bank_account_document(
                tenant_reference, bank_account_reference, existing[0], replayed=True
            )
        record_id = connection.execute(
            """
            INSERT INTO accounting_core.bank_account_record (
                tenant_account_id, bank_account_reference,
                account_currency_code, account_identifier_hash
            )
            VALUES (%s, %s, %s, %s)
            RETURNING bank_account_record_id
            """,
            (tenant_id, bank_account_reference, account_currency_code, identifier_hash),
        ).fetchone()[0]
    return _bank_account_document(
        tenant_reference, bank_account_reference, record_id, replayed=False
    )


def accept_bank_account_assignment(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Bind one bank account to a legal entity, book, and same-book cash chart account.

    The command carries tenant-scoped idempotency identity plus immutable
    command evidence: an exact retry returns the original binding with
    ``replayed=True`` while reuse of the key with different evidence fails
    closed. A second active binding for the same bank account and book is a
    data defect and is rejected at the database scope guard.
    """
    command = _require_command(payload, "a bank-account-assignment command", tenant_reference)
    bank_account_reference = str(command.get("bank_account_reference") or "")
    legal_entity_reference = str(command.get("legal_entity_reference") or "")
    accounting_book_reference = str(
        command.get("accounting_book_reference") or command.get("book_reference") or ""
    )
    chart_account_code = str(command.get("chart_account_code") or "")
    _require_reference(bank_account_reference, "bank account reference")
    _require_reference(legal_entity_reference, "legal entity reference")
    _require_reference(accounting_book_reference, "accounting book reference")
    if not chart_account_code:
        raise AccountingValidationError(
            "chart_account_code is required. "
            "Supply the cash chart account on the same book, then retry the assignment."
        )
    idempotency_key = str(command.get("assignment_idempotency_key") or "")
    if not idempotency_key.strip():
        raise AccountingValidationError(
            "assignment_idempotency_key is required. "
            "Supply the tenant-scoped assignment key, then retry the assignment."
        )
    valid_from = _parse_timestamp(
        str(command.get("valid_from") or ""),
        "assignment valid_from",
    )
    raw_valid_to = command.get("valid_to")
    valid_to = (
        None
        if raw_valid_to in (None, "")
        else _parse_timestamp(str(raw_valid_to), "assignment valid_to")
    )
    command_hash = _assignment_command_hash(
        bank_account_reference=bank_account_reference,
        legal_entity_reference=legal_entity_reference,
        accounting_book_reference=accounting_book_reference,
        chart_account_code=chart_account_code,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(connection, f"bank_assignment:{bank_account_reference}")
        ledger._acquire_command_lock(connection, f"bank_assignment_key:{idempotency_key}")
        prior_command = connection.execute(
            """
            SELECT bank_account_assignment_id, assignment_command_hash,
                   legal_entity_id, accounting_book_id, chart_account_id
            FROM accounting_core.bank_account_assignment
            WHERE tenant_account_id = %s AND assignment_idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior_command is not None:
            if prior_command[1] != command_hash:
                raise IdempotencyConflictError(
                    "assignment idempotency key was already used with a different "
                    "bank-account assignment"
                )
            return _load_assignment_document(
                connection,
                tenant_id,
                tenant_reference,
                prior_command[0],
                replayed=True,
            )
        account_row = _load_bank_account(connection, tenant_id, bank_account_reference)
        legal_entity_id, _currency = ledger._load_legal_entity(
            connection, tenant_id, legal_entity_reference, "the bank-account assignment"
        )
        book_row = connection.execute(
            """
            SELECT accounting_book_id
            FROM accounting_core.accounting_book
            WHERE tenant_account_id = %s
              AND legal_entity_id = %s
              AND book_name = %s
              AND valid_to IS NULL
            """,
            (tenant_id, legal_entity_id, accounting_book_reference),
        ).fetchone()
        if book_row is None:
            raise AccountingValidationError(
                f"Accounting book {accounting_book_reference} is not recorded for this legal entity. "
                "Create the accounting_book row, then retry the assignment."
            )
        chart_row = connection.execute(
            """
            SELECT chart_account_id
            FROM accounting_core.chart_account
            WHERE tenant_account_id = %s
              AND accounting_book_id = %s
              AND chart_account_code = %s
              AND valid_to IS NULL
            """,
            (tenant_id, book_row[0], chart_account_code),
        ).fetchone()
        if chart_row is None:
            raise AccountingValidationError(
                "chart account is not recorded on the selected accounting book. "
                "Select a chart account from that book, then retry the assignment."
            )
        try:
            assignment_id = connection.execute(
                """
                INSERT INTO accounting_core.bank_account_assignment (
                    tenant_account_id, bank_account_record_id, legal_entity_id,
                    accounting_book_id, chart_account_id, valid_from, valid_to,
                    assignment_idempotency_key, assignment_command_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING bank_account_assignment_id
                """,
                (
                    tenant_id,
                    account_row[0],
                    legal_entity_id,
                    book_row[0],
                    chart_row[0],
                    valid_from,
                    valid_to,
                    idempotency_key,
                    command_hash,
                ),
            ).fetchone()[0]
        except Exception as error:
            if _is_foreign_key_error(error):
                raise AccountingValidationError(
                    "bank-account assignment must use a chart account from the same accounting book. "
                    "Select that book's cash chart account, then retry the assignment."
                ) from error
            if _is_unique_violation(error):
                raise AccountingValidationError(
                    "bank account already has an active assignment on that accounting book. "
                    "Close the existing binding with an explicit valid_to, then retry the assignment."
                ) from error
            raise
    return {
        "tenant_reference": tenant_reference,
        "bank_account_reference": bank_account_reference,
        "legal_entity_reference": legal_entity_reference,
        "accounting_book_reference": accounting_book_reference,
        "chart_account_code": chart_account_code,
        "bank_account_assignment_id": str(assignment_id),
        "replayed": False,
    }


def _assignment_command_hash(
    *,
    bank_account_reference: str,
    legal_entity_reference: str,
    accounting_book_reference: str,
    chart_account_code: str,
    valid_from: datetime,
    valid_to: datetime | None,
) -> str:
    """Return the canonical SHA-256 identity of one assignment command's evidence."""

    payload = {
        "bank_account_reference": bank_account_reference,
        "chart_account_code": chart_account_code,
        "accounting_book_reference": accounting_book_reference,
        "legal_entity_reference": legal_entity_reference,
        "valid_from": _format_timestamp(valid_from),
        "valid_to": None if valid_to is None else _format_timestamp(valid_to),
    }
    return _sha256_digest(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _load_assignment_document(
    connection: object,
    tenant_id: UUID,
    tenant_reference: str,
    assignment_id: UUID,
    *,
    replayed: bool,
) -> dict[str, object]:
    """Load one stored assignment back into its public command document shape."""

    row = connection.execute(
        """
        SELECT bank_account.bank_account_reference,
               legal_entity.legal_entity_code,
               accounting_book.book_name,
               chart_account.chart_account_code
        FROM accounting_core.bank_account_assignment AS assignment
        JOIN accounting_core.bank_account_record AS bank_account
            ON bank_account.tenant_account_id = assignment.tenant_account_id
            AND bank_account.bank_account_record_id = assignment.bank_account_record_id
        JOIN accounting_core.legal_entity_record AS legal_entity
            ON legal_entity.tenant_account_id = assignment.tenant_account_id
            AND legal_entity.legal_entity_id = assignment.legal_entity_id
        JOIN accounting_core.accounting_book AS accounting_book
            ON accounting_book.tenant_account_id = assignment.tenant_account_id
            AND accounting_book.accounting_book_id = assignment.accounting_book_id
        JOIN accounting_core.chart_account AS chart_account
            ON chart_account.tenant_account_id = assignment.tenant_account_id
            AND chart_account.chart_account_id = assignment.chart_account_id
        WHERE assignment.tenant_account_id = %s
          AND assignment.bank_account_assignment_id = %s
        """,
        (tenant_id, assignment_id),
    ).fetchone()
    if row is None:
        raise AccountingValidationError(
            "bank-account assignment could not be reloaded after replay. "
            "Repeat the original command, then retry."
        )
    return {
        "tenant_reference": tenant_reference,
        "bank_account_reference": row[0],
        "legal_entity_reference": row[1],
        "accounting_book_reference": row[2],
        "chart_account_code": row[3],
        "bank_account_assignment_id": str(assignment_id),
        "replayed": replayed,
    }


def accept_bank_statement_evidence(
    payload: object,
    database_url: str,
    tenant_reference: str,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, object]:
    """Accept one camt.053.001.14 statement as immutable evidence for *tenant_reference*."""
    command = _require_command(payload, "a bank-statement command", tenant_reference)
    bank_account_reference = str(command.get("bank_account_reference") or "")
    idempotency_key = str(command.get("ingestion_idempotency_key") or "")
    message_definition_identifier = str(command.get("message_definition_identifier") or "")
    statement_payload = command.get("statement_payload")
    _require_reference(bank_account_reference, "bank account reference")
    if not idempotency_key:
        raise AccountingValidationError(
            "ingestion_idempotency_key is required. "
            "Supply the statement ingest key, then retry ingest."
        )
    if not isinstance(statement_payload, str) or not statement_payload:
        raise AccountingValidationError(
            "statement_payload must be the original XML text. "
            "Supply the camt.053.001.14 document, then retry ingest."
        )
    raw_bytes = statement_payload.encode("utf-8")
    source_hash = _sha256_digest(raw_bytes)
    supplied_hash = command.get("source_artifact_hash")
    if supplied_hash not in (None, ""):
        supplied_text = str(supplied_hash)
        if _HASH_PATTERN.fullmatch(supplied_text) is None:
            raise AccountingValidationError(
                "source_artifact_hash must be sha256: plus 64 lowercase hex characters. "
                "Supply that digest, then retry ingest."
            )
        if supplied_text != source_hash:
            raise AccountingValidationError(
                "source_artifact_hash does not match the supplied statement bytes. "
                "Send the original artifact and its SHA-256, then retry ingest."
            )
    statement = parse_bank_statement_payload(raw_bytes, message_definition_identifier)
    store = artifact_store if artifact_store is not None else MemoryArtifactStore()
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        ledger._acquire_command_lock(connection, f"bank_statement:{idempotency_key}")
        ledger._acquire_command_lock(connection, f"bank_statement_hash:{source_hash}")
        account_row = _load_bank_account(connection, tenant_id, bank_account_reference)
        if account_row[1] != statement.account_currency_code:
            raise AccountingValidationError(
                "statement account currency does not match the registered bank account. "
                "Register the matching currency, then retry ingest."
            )
        if account_row[2] != statement.account_identifier_hash:
            raise AccountingValidationError(
                "statement account identifier does not match the registered bank account. "
                "Register the matching account identifier, then retry ingest."
            )
        _require_statement_currencies(statement)
        ledger._acquire_command_lock(
            connection,
            f"bank_statement_identity:{account_row[0]}:{statement.statement_identity_reference}",
        )
        prior_key = connection.execute(
            """
            SELECT bank_statement_record_id, source_artifact_hash, normalized_payload_hash
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = %s AND ingestion_idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior_key is not None:
            if (
                prior_key[1] != statement.source_artifact_hash
                or prior_key[2] != statement.normalized_payload_hash
            ):
                raise IdempotencyConflictError(
                    "ingestion idempotency key was already used with a different statement artifact"
                )
            return _load_statement_document(
                connection, tenant_id, tenant_reference, prior_key[0], replayed=True
            )
        prior_artifact = connection.execute(
            """
            SELECT bank_statement_record_id
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = %s AND source_artifact_hash = %s
            """,
            (tenant_id, statement.source_artifact_hash),
        ).fetchone()
        if prior_artifact is not None:
            return _load_statement_document(
                connection, tenant_id, tenant_reference, prior_artifact[0], replayed=True
            )
        prior_identity = connection.execute(
            """
            SELECT bank_statement_record_id, normalized_payload_hash
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = %s
              AND bank_account_record_id = %s
              AND statement_identity_reference = %s
            """,
            (tenant_id, account_row[0], statement.statement_identity_reference),
        ).fetchone()
        if prior_identity is not None:
            if prior_identity[1] != statement.normalized_payload_hash:
                raise AccountingValidationError(
                    "statement identity already exists with different entry evidence. "
                    "Use an explicit correction contract, then retry ingest."
                )
            return _load_statement_document(
                connection, tenant_id, tenant_reference, prior_identity[0], replayed=True
            )
        locator = store.put_artifact(statement.source_artifact_hash, raw_bytes)
        artifact_id = connection.execute(
            """
            INSERT INTO accounting_integration.bank_statement_artifact (
                tenant_account_id, source_artifact_hash,
                artifact_store_reference, artifact_byte_length
            )
            VALUES (%s, %s, %s, %s)
            RETURNING bank_statement_artifact_id
            """,
            (tenant_id, statement.source_artifact_hash, locator, len(raw_bytes)),
        ).fetchone()[0]
        statement_id = connection.execute(
            """
            INSERT INTO accounting_integration.bank_statement_record (
                tenant_account_id, bank_account_record_id, bank_statement_artifact_id,
                message_definition_identifier, statement_identity_reference,
                electronic_sequence_number, legal_sequence_number,
                period_start_at, period_end_at, opening_balance_hash, closing_balance_hash,
                source_artifact_hash, normalized_payload_hash, ingestion_idempotency_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING bank_statement_record_id
            """,
            (
                tenant_id,
                account_row[0],
                artifact_id,
                statement.message_definition_identifier,
                statement.statement_identity_reference,
                statement.electronic_sequence_number,
                statement.legal_sequence_number,
                statement.period_start_at,
                statement.period_end_at,
                statement.opening_balance_hash,
                statement.closing_balance_hash,
                statement.source_artifact_hash,
                statement.normalized_payload_hash,
                idempotency_key,
            ),
        ).fetchone()[0]
        for balance in statement.balances:
            connection.execute(
                """
                INSERT INTO accounting_integration.bank_statement_balance (
                    tenant_account_id, bank_statement_record_id,
                    balance_sequence_number, balance_type_code, balance_amount,
                    balance_currency_code, credit_debit_code, source_locator_path,
                    source_balance_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    statement_id,
                    balance.balance_sequence_number,
                    balance.balance_type_code,
                    balance.balance_amount,
                    balance.balance_currency_code,
                    balance.credit_debit_code,
                    balance.source_locator_path,
                    balance.source_balance_hash,
                ),
            )
        for entry in statement.entries:
            entry_id = connection.execute(
                """
                INSERT INTO accounting_integration.bank_statement_entry (
                    tenant_account_id, bank_statement_record_id, source_entry_identity,
                    entry_sequence_number, source_locator_path, booking_occurred_at,
                    value_occurred_at, entry_amount, entry_currency_code, credit_debit_code,
                    reversal_indicator, bank_transaction_domain_code,
                    bank_transaction_family_code, bank_transaction_subfamily_code,
                    end_to_end_reference, account_servicer_reference, mandate_reference,
                    cheque_reference, remittance_evidence_text, counterparty_evidence_hash,
                    source_entry_hash
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING bank_statement_entry_id
                """,
                (
                    tenant_id,
                    statement_id,
                    entry.source_entry_identity,
                    entry.entry_sequence_number,
                    entry.source_locator_path,
                    entry.booking_occurred_at,
                    entry.value_occurred_at,
                    entry.entry_amount,
                    entry.entry_currency_code,
                    entry.credit_debit_code,
                    entry.reversal_indicator,
                    entry.bank_transaction_domain_code,
                    entry.bank_transaction_family_code,
                    entry.bank_transaction_subfamily_code,
                    entry.end_to_end_reference,
                    entry.account_servicer_reference,
                    entry.mandate_reference,
                    entry.cheque_reference,
                    entry.remittance_evidence_text,
                    entry.counterparty_evidence_hash,
                    entry.source_entry_hash,
                ),
            ).fetchone()[0]
            for detail in entry.entry_details:
                connection.execute(
                    """
                    INSERT INTO accounting_integration.bank_statement_entry_detail (
                        tenant_account_id, bank_statement_entry_id, detail_sequence_number,
                        source_locator_path, detail_amount, detail_currency_code,
                        credit_debit_code, end_to_end_reference, account_servicer_reference,
                        remittance_evidence_text, source_detail_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id,
                        entry_id,
                        detail.detail_sequence_number,
                        detail.source_locator_path,
                        detail.detail_amount,
                        detail.detail_currency_code,
                        detail.credit_debit_code,
                        detail.end_to_end_reference,
                        detail.account_servicer_reference,
                        detail.remittance_evidence_text,
                        detail.source_detail_hash,
                    ),
                )
        return _load_statement_document(
            connection, tenant_id, tenant_reference, statement_id, replayed=False
        )


def lookup_bank_statements(
    database_url: str,
    tenant_reference: str,
    bank_account_reference: str,
    *,
    period_start: str | None = None,
    period_end: str | None = None,
    page_limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, object]:
    """List tenant-scoped statements for one bank account and optional period window."""
    _require_reference(bank_account_reference, "bank account reference")
    limit = _page_limit(page_limit)
    start_at = (
        None if not period_start else _parse_timestamp(period_start, "period_start")
    )
    end_at = None if not period_end else _parse_timestamp(period_end, "period_end")
    cursor_time, cursor_id = _statement_cursor(cursor)
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        account_row = _load_bank_account(connection, tenant_id, bank_account_reference)
        rows = connection.execute(
            """
            SELECT bank_statement_record_id, statement_identity_reference,
                   message_definition_identifier, source_artifact_hash,
                   normalized_payload_hash, period_start_at, period_end_at, recorded_at
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = %s
              AND bank_account_record_id = %s
              AND (%s OR period_start_at IS NULL OR period_start_at >= %s)
              AND (%s OR period_end_at IS NULL OR period_end_at <= %s)
              AND (
                    %s
                    OR recorded_at > %s
                    OR (recorded_at = %s AND bank_statement_record_id > %s)
                  )
            ORDER BY recorded_at ASC, bank_statement_record_id ASC
            LIMIT %s
            """,
            (
                tenant_id,
                account_row[0],
                start_at is None,
                start_at,
                end_at is None,
                end_at,
                cursor_id is None,
                cursor_time,
                cursor_time,
                cursor_id,
                limit + 1,
            ),
        ).fetchall()
    page = rows[:limit]
    documents = [
        {
            "tenant_reference": tenant_reference,
            "bank_account_reference": bank_account_reference,
            "bank_statement_record_id": str(row[0]),
            "statement_identity_reference": row[1],
            "message_definition_identifier": row[2],
            "source_artifact_hash": row[3],
            "normalized_payload_hash": row[4],
            "period_start_at": None if row[5] is None else _format_timestamp(row[5]),
            "period_end_at": None if row[6] is None else _format_timestamp(row[6]),
        }
        for row in page
    ]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = f"{_format_timestamp(last[7])}|{last[0]}"
    return {
        "tenant_reference": tenant_reference,
        "bank_account_reference": bank_account_reference,
        "bank_statements": documents,
        "next_cursor": next_cursor,
    }


def lookup_bank_statement(
    database_url: str,
    tenant_reference: str,
    bank_statement_record_id: str,
) -> dict[str, object]:
    """Return one statement and its exact normalized debit/credit totals."""
    statement_id = _require_uuid(bank_statement_record_id, "bank_statement_record_id")
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        return _load_statement_document(
            connection, tenant_id, tenant_reference, statement_id, replayed=False
        )


def lookup_bank_statement_entries(
    database_url: str,
    tenant_reference: str,
    bank_statement_record_id: str,
    *,
    page_limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, object]:
    """List normalized entries for one statement with stable sequence pagination."""
    statement_id = _require_uuid(bank_statement_record_id, "bank_statement_record_id")
    limit = _page_limit(page_limit)
    after_sequence = _entry_cursor(cursor)
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        header = connection.execute(
            """
            SELECT bank_statement_record_id
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = %s AND bank_statement_record_id = %s
            """,
            (tenant_id, statement_id),
        ).fetchone()
        if header is None:
            raise AccountingValidationError(
                "bank statement is not recorded for this tenant. "
                "Supply a persisted bank_statement_record_id, then retry the entry list."
            )
        rows = connection.execute(
            """
            SELECT bank_statement_entry_id, source_entry_identity, entry_sequence_number,
                   source_locator_path, booking_occurred_at, value_occurred_at,
                   entry_amount, entry_currency_code, credit_debit_code, reversal_indicator,
                   bank_transaction_domain_code, bank_transaction_family_code,
                   bank_transaction_subfamily_code, end_to_end_reference,
                   account_servicer_reference, mandate_reference, cheque_reference,
                   remittance_evidence_text, counterparty_evidence_hash, source_entry_hash
            FROM accounting_integration.bank_statement_entry
            WHERE tenant_account_id = %s
              AND bank_statement_record_id = %s
              AND entry_sequence_number > %s
            ORDER BY entry_sequence_number ASC
            LIMIT %s
            """,
            (tenant_id, statement_id, after_sequence, limit + 1),
        ).fetchall()
        page = rows[:limit]
        documents = [_entry_document(connection, tenant_id, row) for row in page]
    next_cursor = None if len(rows) <= limit else str(page[-1][2])
    return {
        "tenant_reference": tenant_reference,
        "bank_statement_record_id": str(statement_id),
        "bank_statement_entries": documents,
        "next_cursor": next_cursor,
    }


def _require_command(
    payload: object, supply_what: str, tenant_reference: str
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            f"command payload must be a JSON object. Supply {supply_what}, then retry."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "command tenant_reference does not match the bound tenant. "
            "Call the command with that tenant_reference, then retry."
        )
    return payload


def _account_identifier_hash(command: Mapping[str, object]) -> str:
    supplied_hash = str(command.get("account_identifier_hash") or "")
    identifier = command.get("account_identifier")
    if supplied_hash:
        if _HASH_PATTERN.fullmatch(supplied_hash) is None:
            raise AccountingValidationError(
                "account_identifier_hash must be sha256: plus 64 lowercase hex characters. "
                "Supply that digest, then retry the account register."
            )
        return supplied_hash
    if not isinstance(identifier, str) or not identifier:
        raise AccountingValidationError(
            "account_identifier_hash is required unless a one-time account_identifier is hashed. "
            "Supply the hash, then retry the account register."
        )
    return _sha256_digest(identifier.encode("utf-8"))


def _bank_account_document(
    tenant_reference: str,
    bank_account_reference: str,
    record_id: UUID,
    *,
    replayed: bool,
) -> dict[str, object]:
    return {
        "tenant_reference": tenant_reference,
        "bank_account_reference": bank_account_reference,
        "bank_account_record_id": str(record_id),
        "replayed": replayed,
    }


def _load_bank_account(
    connection: object, tenant_id: UUID, bank_account_reference: str
) -> tuple[UUID, str, str]:
    row = connection.execute(
        """
        SELECT bank_account_record_id, account_currency_code, account_identifier_hash
        FROM accounting_core.bank_account_record
        WHERE tenant_account_id = %s AND bank_account_reference = %s
        """,
        (tenant_id, bank_account_reference),
    ).fetchone()
    if row is None:
        raise AccountingValidationError(
            "bank account is not recorded for this tenant. "
            "Register the bank account, then retry."
        )
    return row[0], row[1], row[2]


def _load_statement_document(
    connection: object,
    tenant_id: UUID,
    tenant_reference: str,
    statement_id: UUID,
    *,
    replayed: bool,
) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT statement.bank_statement_record_id,
               statement.statement_identity_reference,
               statement.message_definition_identifier,
               statement.source_artifact_hash,
               statement.normalized_payload_hash,
               statement.electronic_sequence_number,
               statement.legal_sequence_number,
               statement.period_start_at,
               statement.period_end_at,
               statement.opening_balance_hash,
               statement.closing_balance_hash,
               artifact.artifact_store_reference,
               account.bank_account_reference
        FROM accounting_integration.bank_statement_record AS statement
        JOIN accounting_integration.bank_statement_artifact AS artifact
          ON artifact.tenant_account_id = statement.tenant_account_id
         AND artifact.bank_statement_artifact_id = statement.bank_statement_artifact_id
        JOIN accounting_core.bank_account_record AS account
          ON account.tenant_account_id = statement.tenant_account_id
         AND account.bank_account_record_id = statement.bank_account_record_id
        WHERE statement.tenant_account_id = %s
          AND statement.bank_statement_record_id = %s
        """,
        (tenant_id, statement_id),
    ).fetchone()
    if row is None:
        raise AccountingValidationError(
            "bank statement is not recorded for this tenant. "
            "Supply a persisted bank_statement_record_id, then retry the statement read."
        )
    totals = connection.execute(
        """
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN credit_debit_code = 'CRDT' THEN entry_amount ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN credit_debit_code = 'DBIT' THEN entry_amount ELSE 0 END), 0)
        FROM accounting_integration.bank_statement_entry
        WHERE tenant_account_id = %s AND bank_statement_record_id = %s
        """,
        (tenant_id, statement_id),
    ).fetchone()
    balance_rows = connection.execute(
        """
        SELECT balance_sequence_number, balance_type_code, balance_amount,
               balance_currency_code, credit_debit_code, source_locator_path,
               source_balance_hash
        FROM accounting_integration.bank_statement_balance
        WHERE tenant_account_id = %s AND bank_statement_record_id = %s
        ORDER BY balance_sequence_number ASC
        """,
        (tenant_id, statement_id),
    ).fetchall()
    return {
        "tenant_reference": tenant_reference,
        "bank_account_reference": row[12],
        "bank_statement_record_id": str(row[0]),
        "statement_identity_reference": row[1],
        "message_definition_identifier": row[2],
        "source_artifact_hash": row[3],
        "normalized_payload_hash": row[4],
        "electronic_sequence_number": row[5],
        "legal_sequence_number": row[6],
        "period_start_at": None if row[7] is None else _format_timestamp(row[7]),
        "period_end_at": None if row[8] is None else _format_timestamp(row[8]),
        "opening_balance_hash": row[9],
        "closing_balance_hash": row[10],
        "artifact_store_reference": row[11],
        "balances": [
            {
                "balance_sequence_number": int(balance[0]),
                "balance_type_code": balance[1],
                "balance_amount": _decimal_text(balance[2]),
                "balance_currency_code": balance[3],
                "credit_debit_code": balance[4],
                "source_locator_path": balance[5],
                "source_balance_hash": balance[6],
            }
            for balance in balance_rows
        ],
        "entry_count": int(totals[0]),
        "credit_total_amount": _decimal_text(totals[1]),
        "debit_total_amount": _decimal_text(totals[2]),
        "replayed": replayed,
    }


def _entry_document(
    connection: object, tenant_id: UUID, row: tuple[object, ...]
) -> dict[str, object]:
    details = connection.execute(
        """
        SELECT detail_sequence_number, source_locator_path, detail_amount,
               detail_currency_code, credit_debit_code, end_to_end_reference,
               remittance_evidence_text, source_detail_hash
        FROM accounting_integration.bank_statement_entry_detail
        WHERE tenant_account_id = %s AND bank_statement_entry_id = %s
        ORDER BY detail_sequence_number ASC
        """,
        (tenant_id, row[0]),
    ).fetchall()
    return {
        "bank_statement_entry_id": str(row[0]),
        "source_entry_identity": row[1],
        "entry_sequence_number": int(row[2]),
        "source_locator_path": row[3],
        "booking_occurred_at": None if row[4] is None else _format_timestamp(row[4]),
        "value_occurred_at": None if row[5] is None else _format_timestamp(row[5]),
        "entry_amount": _decimal_text(row[6]),
        "entry_currency_code": row[7],
        "credit_debit_code": row[8],
        "reversal_indicator": bool(row[9]),
        "bank_transaction_domain_code": row[10],
        "bank_transaction_family_code": row[11],
        "bank_transaction_subfamily_code": row[12],
        "end_to_end_reference": row[13],
        "account_servicer_reference": row[14],
        "mandate_reference": row[15],
        "cheque_reference": row[16],
        "remittance_evidence_text": row[17],
        "counterparty_evidence_hash": row[18],
        "source_entry_hash": row[19],
        "entry_details": [
            {
                "detail_sequence_number": int(detail[0]),
                "source_locator_path": detail[1],
                "detail_amount": _decimal_text(detail[2]),
                "detail_currency_code": detail[3],
                "credit_debit_code": detail[4],
                "end_to_end_reference": detail[5],
                "remittance_evidence_text": detail[6],
                "source_detail_hash": detail[7],
            }
            for detail in details
        ],
    }


def _parse_bounded_xml(payload: bytes) -> "_XmlElement":
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.ordered_attributes = True
    stack: list[_XmlElement] = []
    root_holder: list[_XmlElement | None] = [None]
    stats = {"depth": 0, "elements": 0, "attributes": 0, "text_bytes": 0}

    def _reject_external(*_args: object) -> int:
        raise AccountingValidationError(
            "external XML entity or DTD resolution is not permitted. "
            "Remove DTD and external entities, then retry ingest."
        )

    def _reject_doctype(*_args: object) -> None:
        raise AccountingValidationError(
            "XML DTD declarations are not permitted. "
            "Remove the DTD, then retry ingest."
        )

    def _reject_entity(*_args: object) -> None:
        raise AccountingValidationError(
            "XML entity declarations are not permitted. "
            "Remove entity declarations, then retry ingest."
        )

    def _reject_pi(target: str, _data: str) -> None:
        if target.lower() in {"xml-stylesheet", "xsl", "xslt"}:
            raise AccountingValidationError(
                "XML stylesheet processing is not permitted. "
                "Remove processing instructions, then retry ingest."
            )
        raise AccountingValidationError(
            "XML processing instructions are not permitted. "
            "Remove processing instructions, then retry ingest."
        )

    def _start_element(name: str, attributes: object) -> None:
        attrib = (
            {str(key): str(value) for key, value in attributes.items()}
            if isinstance(attributes, dict)
            else {
                attributes[index]: attributes[index + 1]
                for index in range(0, len(attributes), 2)
            }
        )
        stats["depth"] += 1
        stats["elements"] += 1
        stats["attributes"] += len(attrib)
        if stats["depth"] > MAX_XML_DEPTH:
            raise AccountingValidationError(
                "XML depth exceeds the adapter bound. "
                "Send a shallower statement document, then retry ingest."
            )
        if stats["elements"] > MAX_ELEMENT_COUNT:
            raise AccountingValidationError(
                "XML element count exceeds the adapter bound. "
                "Send a smaller statement document, then retry ingest."
            )
        if stats["attributes"] > MAX_ATTRIBUTE_COUNT:
            raise AccountingValidationError(
                "XML attribute count exceeds the adapter bound. "
                "Send a smaller statement document, then retry ingest."
            )
        namespace, local_name = _split_expat_name(name)
        element = _XmlElement(local_name, namespace, attrib, "", [])
        if stack:
            stack[-1].children.append(element)
        else:
            root_holder[0] = element
        stack.append(element)

    def _end_element(_name: str) -> None:
        stack.pop()
        stats["depth"] -= 1

    def _handle_text(text: str) -> None:
        encoded = text.encode("utf-8")
        stats["text_bytes"] += len(encoded)
        if stats["text_bytes"] > MAX_TEXT_BYTES:
            raise AccountingValidationError(
                "XML text length exceeds the adapter bound. "
                "Send a smaller statement document, then retry ingest."
            )
        if stack:
            stack[-1].text += text

    parser.StartDoctypeDeclHandler = _reject_doctype
    parser.EntityDeclHandler = _reject_entity
    parser.UnparsedEntityDeclHandler = _reject_entity
    parser.ExternalEntityRefHandler = _reject_external
    parser.ProcessingInstructionHandler = _reject_pi
    parser.StartElementHandler = _start_element
    parser.EndElementHandler = _end_element
    parser.CharacterDataHandler = _handle_text
    try:
        parser.Parse(payload, True)
    except AccountingValidationError:
        raise
    except expat.ExpatError as error:
        raise AccountingValidationError(
            "statement XML is not well formed. "
            "Supply a well-formed camt.053.001.14 document, then retry ingest."
        ) from error
    root = root_holder[0]
    if root is None:
        raise AccountingValidationError(
            "statement XML has no document element. "
            "Supply a Document root, then retry ingest."
        )
    return root


def _split_expat_name(name: str) -> tuple[str, str]:
    if " " not in name:
        return "", name
    namespace, local_name = name.split(" ", 1)
    return namespace, local_name


def _reject_revision_mismatch(document: "_XmlElement") -> None:
    if document.local_name != "Document" or document.namespace != CAMT053_NAMESPACE:
        raise AccountingValidationError(
            "message-definition revision is not camt.053.001.14. "
            "Send BankToCustomerStatementV14, then retry ingest."
        )


def _require_structural_paths(document: "_XmlElement", required_paths: tuple[str, ...]) -> None:
    for path in required_paths:
        if _find_path(document, path.split("/")) is None:
            raise AccountingValidationError(
                "statement is missing a required camt.053.001.14 path. "
                "Supply a complete BankToCustomerStatementV14 document, then retry ingest."
            )


def _find_path(element: "_XmlElement", parts: list[str]) -> "_XmlElement | None":
    if not parts:
        return element
    expected = parts[0]
    if element.local_name != expected:
        return None
    if len(parts) == 1:
        return element
    for child in element.children:
        found = _find_path(child, parts[1:])
        if found is not None:
            return found
    return None


def _normalize_statement(statement: "_XmlElement", payload: bytes) -> NormalizedBankStatement:
    identity = _required_text(statement, ("Id",), "statement identity")
    account = _required_child(statement, "Acct", "statement account")
    currency = _required_text(account, ("Ccy",), "account currency")
    _require_currency(currency)
    account_id_text = _first_text(account, ("Id", "IBAN")) or _first_text(
        account, ("Id", "Othr", "Id")
    )
    if not account_id_text:
        raise AccountingValidationError(
            "statement account identifier is missing. "
            "Supply Acct/Id, then retry ingest."
        )
    entries = []
    for index, entry_node in enumerate(_direct_children(statement, "Ntry"), start=1):
        if index > MAX_ENTRY_COUNT:
            raise AccountingValidationError(
                "statement entry count exceeds the adapter bound. "
                "Send fewer entries, then retry ingest."
            )
        entries.append(_normalize_entry(entry_node, index))
    if not entries:
        raise AccountingValidationError(
            "statement contains no Ntry elements. "
            "Supply at least one entry, then retry ingest."
        )
    balances = [
        _normalize_balance(node, index)
        for index, node in enumerate(_direct_children(statement, "Bal"), start=1)
    ]
    opening = next(
        (item.source_balance_hash for item in balances if item.balance_type_code == "OPBD"),
        None,
    )
    closing = next(
        (item.source_balance_hash for item in balances if item.balance_type_code == "CLBD"),
        None,
    )
    period = _required_child(statement, "FrToDt", "statement period") if _direct_children(statement, "FrToDt") else None
    normalized = NormalizedBankStatement(
        message_definition_identifier=CAMT053_MESSAGE_DEFINITION,
        statement_identity_reference=identity,
        electronic_sequence_number=_first_text(statement, ("ElctrncSeqNb",)),
        legal_sequence_number=_first_text(statement, ("LglSeqNb",)),
        period_start_at=None if period is None else _optional_timestamp(_first_text(period, ("FrDtTm",))),
        period_end_at=None if period is None else _optional_timestamp(_first_text(period, ("ToDtTm",))),
        opening_balance_hash=opening,
        closing_balance_hash=closing,
        account_currency_code=currency,
        account_identifier_hash=_sha256_digest(account_id_text.encode("utf-8")),
        source_artifact_hash=_sha256_digest(payload),
        normalized_payload_hash="",
        balances=tuple(balances),
        entries=tuple(entries),
    )
    digest = _sha256_digest(
        json.dumps(_normalized_payload(normalized), separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    )
    return NormalizedBankStatement(
        message_definition_identifier=normalized.message_definition_identifier,
        statement_identity_reference=normalized.statement_identity_reference,
        electronic_sequence_number=normalized.electronic_sequence_number,
        legal_sequence_number=normalized.legal_sequence_number,
        period_start_at=normalized.period_start_at,
        period_end_at=normalized.period_end_at,
        opening_balance_hash=normalized.opening_balance_hash,
        closing_balance_hash=normalized.closing_balance_hash,
        account_currency_code=normalized.account_currency_code,
        account_identifier_hash=normalized.account_identifier_hash,
        source_artifact_hash=normalized.source_artifact_hash,
        normalized_payload_hash=digest,
        balances=normalized.balances,
        entries=normalized.entries,
    )


def _require_statement_currencies(statement: NormalizedBankStatement) -> None:
    """Fail closed when statement facts leave the registered account scope.

    Foreign-exchange accounting is explicitly rejected until rate source, rate
    type, rounding, remeasurement, and translation policy exist (TRD), so every
    entry on an accepted statement must carry the statement's own currency.
    """

    for balance in statement.balances:
        if balance.balance_currency_code != statement.account_currency_code:
            raise AccountingValidationError(
                f"statement balance {balance.balance_sequence_number} uses currency "
                f"{balance.balance_currency_code}, but this registry accepts only "
                f"{statement.account_currency_code} evidence. Register a matching-currency "
                "account or correct the statement, then retry ingest."
            )
    for entry in statement.entries:
        if entry.entry_currency_code != statement.account_currency_code:
            raise AccountingValidationError(
                f"statement entry {entry.entry_sequence_number} uses currency "
                f"{entry.entry_currency_code}, but this registry accepts only "
                f"{statement.account_currency_code} evidence. Register a matching-currency "
                "account or correct the statement, then retry ingest."
            )


def _normalize_entry(entry_node: "_XmlElement", sequence: int) -> NormalizedStatementEntry:
    amount, currency = _required_amount(entry_node, "Ntry/Amt")
    credit_debit = _required_text(entry_node, ("CdtDbtInd",), "credit/debit indicator")
    if credit_debit not in {"CRDT", "DBIT"}:
        raise AccountingValidationError(
            "credit/debit indicator must be CRDT or DBIT. "
            "Correct CdtDbtInd, then retry ingest."
        )
    locator = f"Document/BkToCstmrStmt/Stmt/Ntry[{sequence}]"
    details = []
    for detail_index, detail_node in enumerate(
        _child_elements(entry_node, "NtryDtls", "TxDtls"), start=1
    ):
        details.append(_normalize_detail(detail_node, sequence, detail_index, credit_debit))
    remittance = _bound_text(_first_text(entry_node, ("NtryDtls", "TxDtls", "RmtInf", "Ustrd")))
    debtor_name = _first_text(entry_node, ("NtryDtls", "TxDtls", "RltdPties", "Dbtr", "Pty", "Nm"))
    creditor_name = _first_text(
        entry_node, ("NtryDtls", "TxDtls", "RltdPties", "Cdtr", "Pty", "Nm")
    )
    # camt.053 CdtDbtInd is expressed from the account owner's perspective:
    # a CRDT entry records money received from the payer (Dbtr), while a DBIT
    # entry records money sent to the payee (Cdtr). The counterparty evidence
    # therefore follows the entry direction before falling back to whatever
    # party the statement supplies.
    if credit_debit == "CRDT":
        counterparty = debtor_name if debtor_name is not None else creditor_name
    else:
        counterparty = creditor_name if creditor_name is not None else debtor_name
    domain = _first_text(entry_node, ("BkTxCd", "Domn", "Cd"))
    family = _first_text(entry_node, ("BkTxCd", "Domn", "Fmly", "Cd"))
    subfamily = _first_text(entry_node, ("BkTxCd", "Domn", "Fmly", "SubFmlyCd"))
    entry = NormalizedStatementEntry(
        source_entry_identity=_first_text(entry_node, ("NtryRef",)),
        entry_sequence_number=sequence,
        source_locator_path=locator,
        booking_occurred_at=_optional_timestamp(
            _first_text(entry_node, ("BookgDt", "DtTm"))
            or _first_text(entry_node, ("BookgDt", "Dt"))
        ),
        value_occurred_at=_optional_timestamp(
            _first_text(entry_node, ("ValDt", "DtTm")) or _first_text(entry_node, ("ValDt", "Dt"))
        ),
        entry_amount=amount,
        entry_currency_code=currency,
        credit_debit_code=credit_debit,
        reversal_indicator=_parse_reversal(_first_text(entry_node, ("RvslInd",))),
        bank_transaction_domain_code=domain,
        bank_transaction_family_code=family,
        bank_transaction_subfamily_code=subfamily,
        end_to_end_reference=_first_text(entry_node, ("NtryDtls", "TxDtls", "Refs", "EndToEndId")),
        account_servicer_reference=_first_text(entry_node, ("AcctSvcrRef",)),
        mandate_reference=_first_text(entry_node, ("NtryDtls", "TxDtls", "Refs", "MndtId")),
        cheque_reference=_first_text(entry_node, ("NtryDtls", "TxDtls", "Refs", "ChqNb")),
        remittance_evidence_text=remittance,
        counterparty_evidence_hash=(
            None if counterparty is None else _sha256_digest(counterparty.encode("utf-8"))
        ),
        source_entry_hash="",
        entry_details=tuple(details),
    )
    digest = _sha256_digest(
        json.dumps(_entry_payload(entry), separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return NormalizedStatementEntry(
        source_entry_identity=entry.source_entry_identity,
        entry_sequence_number=entry.entry_sequence_number,
        source_locator_path=entry.source_locator_path,
        booking_occurred_at=entry.booking_occurred_at,
        value_occurred_at=entry.value_occurred_at,
        entry_amount=entry.entry_amount,
        entry_currency_code=entry.entry_currency_code,
        credit_debit_code=entry.credit_debit_code,
        reversal_indicator=entry.reversal_indicator,
        bank_transaction_domain_code=entry.bank_transaction_domain_code,
        bank_transaction_family_code=entry.bank_transaction_family_code,
        bank_transaction_subfamily_code=entry.bank_transaction_subfamily_code,
        end_to_end_reference=entry.end_to_end_reference,
        account_servicer_reference=entry.account_servicer_reference,
        mandate_reference=entry.mandate_reference,
        cheque_reference=entry.cheque_reference,
        remittance_evidence_text=entry.remittance_evidence_text,
        counterparty_evidence_hash=entry.counterparty_evidence_hash,
        source_entry_hash=digest,
        entry_details=entry.entry_details,
    )


def _normalize_detail(
    detail_node: "_XmlElement",
    entry_sequence: int,
    detail_sequence: int,
    fallback_credit_debit: str,
) -> NormalizedEntryDetail:
    amount_node = _find_path(detail_node, ["TxDtls", "AmtDtls", "TxAmt", "Amt"])
    if amount_node is None:
        raise AccountingValidationError(
            "transaction detail is missing an exact amount. "
            "Supply TxDtls/AmtDtls/TxAmt/Amt, then retry ingest."
        )
    amount, currency = _amount_from_element(amount_node, "TxDtls/AmtDtls/TxAmt/Amt")
    locator = (
        f"Document/BkToCstmrStmt/Stmt/Ntry[{entry_sequence}]/NtryDtls/TxDtls[{detail_sequence}]"
    )
    remittance = _bound_text(_first_text(detail_node, ("RmtInf", "Ustrd")))
    credit_debit_code = _first_text(detail_node, ("CdtDbtInd",)) or fallback_credit_debit
    if credit_debit_code not in {"CRDT", "DBIT"}:
        raise AccountingValidationError(
            "credit/debit indicator must be CRDT or DBIT. "
            "Correct CdtDbtInd, then retry ingest."
        )
    detail = NormalizedEntryDetail(
        detail_sequence_number=detail_sequence,
        source_locator_path=locator,
        detail_amount=amount,
        detail_currency_code=currency,
        credit_debit_code=credit_debit_code,
        end_to_end_reference=_first_text(detail_node, ("Refs", "EndToEndId")),
        account_servicer_reference=_first_text(detail_node, ("Refs", "AcctSvcrRef")),
        remittance_evidence_text=remittance,
        source_detail_hash="",
    )
    digest = _sha256_digest(
        json.dumps(
            {
                "locator": detail.source_locator_path,
                "amount": _decimal_text(detail.detail_amount),
                "currency": detail.detail_currency_code,
                "credit_debit": detail.credit_debit_code,
                "end_to_end": detail.end_to_end_reference,
                "remittance": detail.remittance_evidence_text,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return NormalizedEntryDetail(
        detail_sequence_number=detail.detail_sequence_number,
        source_locator_path=detail.source_locator_path,
        detail_amount=detail.detail_amount,
        detail_currency_code=detail.detail_currency_code,
        credit_debit_code=detail.credit_debit_code,
        end_to_end_reference=detail.end_to_end_reference,
        account_servicer_reference=detail.account_servicer_reference,
        remittance_evidence_text=detail.remittance_evidence_text,
        source_detail_hash=digest,
    )


def _normalize_balance(
    node: "_XmlElement", sequence: int
) -> NormalizedStatementBalance:
    code = _first_text(node, ("Tp", "CdOrPrtry", "Cd"))
    amount, currency = _required_amount(node, "Bal/Amt", allow_zero=True)
    indicator = _required_text(node, ("CdtDbtInd",), "balance credit/debit indicator")
    if indicator not in {"CRDT", "DBIT"}:
        raise AccountingValidationError(
            "balance credit/debit indicator must be CRDT or DBIT. "
            "Correct Bal/CdtDbtInd, then retry ingest."
        )
    source_hash = _sha256_digest(
        json.dumps(
            {
                "code": code,
                "amount": _decimal_text(amount),
                "currency": currency,
                "credit_debit": indicator,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return NormalizedStatementBalance(
        balance_sequence_number=sequence,
        balance_type_code=code,
        balance_amount=amount,
        balance_currency_code=currency,
        credit_debit_code=indicator,
        source_locator_path=f"Document/BkToCstmrStmt/Stmt/Bal[{sequence}]",
        source_balance_hash=source_hash,
    )


def _normalized_payload(statement: NormalizedBankStatement) -> dict[str, object]:
    return {
        "message_definition_identifier": statement.message_definition_identifier,
        "statement_identity_reference": statement.statement_identity_reference,
        "electronic_sequence_number": statement.electronic_sequence_number,
        "legal_sequence_number": statement.legal_sequence_number,
        "period_start_at": None
        if statement.period_start_at is None
        else _format_timestamp(statement.period_start_at),
        "period_end_at": None
        if statement.period_end_at is None
        else _format_timestamp(statement.period_end_at),
        "opening_balance_hash": statement.opening_balance_hash,
        "closing_balance_hash": statement.closing_balance_hash,
        "account_currency_code": statement.account_currency_code,
        "balances": [_balance_payload(balance) for balance in statement.balances],
        "entries": [_entry_payload(entry) for entry in statement.entries],
    }


def _balance_payload(balance: NormalizedStatementBalance) -> dict[str, object]:
    return {
        "balance_sequence_number": balance.balance_sequence_number,
        "balance_type_code": balance.balance_type_code,
        "balance_amount": _decimal_text(balance.balance_amount),
        "balance_currency_code": balance.balance_currency_code,
        "credit_debit_code": balance.credit_debit_code,
        "source_locator_path": balance.source_locator_path,
        "source_balance_hash": balance.source_balance_hash,
    }


def _entry_payload(entry: NormalizedStatementEntry) -> dict[str, object]:
    return {
        "source_entry_identity": entry.source_entry_identity,
        "entry_sequence_number": entry.entry_sequence_number,
        "source_locator_path": entry.source_locator_path,
        "booking_occurred_at": None
        if entry.booking_occurred_at is None
        else _format_timestamp(entry.booking_occurred_at),
        "value_occurred_at": None
        if entry.value_occurred_at is None
        else _format_timestamp(entry.value_occurred_at),
        "entry_amount": _decimal_text(entry.entry_amount),
        "entry_currency_code": entry.entry_currency_code,
        "credit_debit_code": entry.credit_debit_code,
        "reversal_indicator": entry.reversal_indicator,
        "bank_transaction_domain_code": entry.bank_transaction_domain_code,
        "bank_transaction_family_code": entry.bank_transaction_family_code,
        "bank_transaction_subfamily_code": entry.bank_transaction_subfamily_code,
        "end_to_end_reference": entry.end_to_end_reference,
        "account_servicer_reference": entry.account_servicer_reference,
        "mandate_reference": entry.mandate_reference,
        "cheque_reference": entry.cheque_reference,
        "remittance_evidence_text": entry.remittance_evidence_text,
        "counterparty_evidence_hash": entry.counterparty_evidence_hash,
        "details": [
            {
                "detail_sequence_number": detail.detail_sequence_number,
                "source_locator_path": detail.source_locator_path,
                "detail_amount": _decimal_text(detail.detail_amount),
                "detail_currency_code": detail.detail_currency_code,
                "credit_debit_code": detail.credit_debit_code,
                "end_to_end_reference": detail.end_to_end_reference,
                "remittance_evidence_text": detail.remittance_evidence_text,
            }
            for detail in entry.entry_details
        ],
    }


def _required_amount(
    parent: "_XmlElement", label: str, *, allow_zero: bool = False
) -> tuple[Decimal, str]:
    amount_node = _direct_children(parent, "Amt")
    if not amount_node:
        raise AccountingValidationError(
            f"{label} is missing. Supply Amt and Ccy, then retry ingest."
        )
    return _amount_from_element(amount_node[0], label, allow_zero=allow_zero)


def _amount_from_element(
    amount_node: "_XmlElement", label: str, *, allow_zero: bool = False
) -> tuple[Decimal, str]:
    currency = amount_node.attributes.get("Ccy", "")
    try:
        _require_currency(currency)
    except AccountingValidationError as error:
        raise AccountingValidationError(
            f"{label} currency is missing or invalid. Supply a three-letter Ccy, then retry ingest."
        ) from error
    text = amount_node.text.strip()
    try:
        amount = _parse_amount(text)
    except AccountingValidationError as error:
        raise AccountingValidationError(
            f"{label} must be an exact decimal with at most six fractional digits. "
            "Correct the amount, then retry ingest."
        ) from error
    if amount == 0 and not allow_zero:
        raise AccountingValidationError(
            f"{label} must be greater than zero. Correct the amount, then retry ingest."
        )
    return amount, currency


def _required_child(parent: "_XmlElement", name: str, label: str) -> "_XmlElement":
    children = _direct_children(parent, name)
    if not children:
        raise AccountingValidationError(
            f"{label} is missing. Supply {name}, then retry ingest."
        )
    return children[0]


def _required_text(parent: "_XmlElement", path: tuple[str, ...], label: str) -> str:
    text = _first_text(parent, path)
    if not text:
        raise AccountingValidationError(
            f"{label} is missing. Supply {'/'.join(path)}, then retry ingest."
        )
    return text


def _first_text(parent: "_XmlElement", path: tuple[str, ...]) -> str | None:
    node: _XmlElement | None = parent
    for name in path:
        if node is None:
            return None
        children = _direct_children(node, name)
        node = children[0] if children else None
    if node is None:
        return None
    text = node.text.strip()
    return text or None


def _child_elements(parent: "_XmlElement", *path: str) -> list["_XmlElement"]:
    nodes = [parent]
    for name in path:
        next_nodes: list[_XmlElement] = []
        for node in nodes:
            next_nodes.extend(_direct_children(node, name))
        nodes = next_nodes
    return nodes


def _direct_children(parent: "_XmlElement", name: str) -> list["_XmlElement"]:
    return [child for child in parent.children if child.local_name == name]


def _parse_reversal(value: str | None) -> bool:
    if value is None:
        return False
    lowered = value.strip().lower()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    raise AccountingValidationError(
        "reversal indicator must be true or false. Correct RvslInd, then retry ingest."
    )


def _bound_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:MAX_REMITTANCE_CHARS]


def _optional_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_timestamp(value, "statement timestamp")


def _parse_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingValidationError(
            f"{label} must be an ISO-8601 date or timestamp. "
            "Supply a UTC timestamp, then retry."
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _page_limit(page_limit: int | None) -> int:
    if page_limit is None:
        return _PAGE_DEFAULT
    if page_limit < 1 or page_limit > _PAGE_MAXIMUM:
        raise AccountingValidationError(
            "page_limit must be an integer from 1 to 100. "
            "Supply a valid page_limit, then retry the list."
        )
    return page_limit


def _statement_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if not cursor:
        return None, None
    try:
        stamp, record_id = cursor.split("|", 1)
        return _parse_timestamp(stamp, "cursor"), UUID(record_id)
    except (TypeError, ValueError) as error:
        raise AccountingValidationError(
            "cursor must be recorded_at|bank_statement_record_id. "
            "Supply the next_cursor from the previous page, then retry the list."
        ) from error


def _entry_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except ValueError as error:
        raise AccountingValidationError(
            "cursor must be the last entry_sequence_number. "
            "Supply the next_cursor from the previous page, then retry the entry list."
        ) from error
    if value < 0:
        raise AccountingValidationError(
            "cursor must be the last entry_sequence_number. "
            "Supply the next_cursor from the previous page, then retry the entry list."
        )
    return value


def _require_uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise AccountingValidationError(
            f"{label} must be a UUID. Supply the persisted identifier, then retry."
        ) from error


def _sha256_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _decimal_text(value: object) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _is_foreign_key_error(error: BaseException) -> bool:
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate == "23503":
        return True
    cause = error.__cause__
    return cause is not None and getattr(cause, "sqlstate", None) == "23503"


def _is_unique_violation(error: BaseException) -> bool:
    """Return whether *error* is PostgreSQL SQLSTATE 23505 at any cause depth."""

    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate == "23505":
        return True
    cause = error.__cause__
    return cause is not None and getattr(cause, "sqlstate", None) == "23505"


@dataclass
class _XmlElement:
    local_name: str
    namespace: str
    attributes: dict[str, str]
    text: str
    children: list[_XmlElement]
