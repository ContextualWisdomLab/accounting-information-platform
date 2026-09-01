"""Contracts for immutable maker-checker reconciliation exception resolution."""

from __future__ import annotations

import contextlib
import unittest
import unittest.mock as mock
from datetime import datetime, timezone
from uuid import UUID

from accounting_information_platform import AccountingValidationError, IdempotencyConflictError
from accounting_information_platform import reconciliation_exception_resolution as resolution

_TENANT = "urn:cwl:tenant:test"
_TENANT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
_EXCEPTION_ID = UUID("22222222-2222-2222-2222-222222222222")
_RESOLUTION_ID = UUID("33333333-3333-3333-3333-333333333333")
_EXCEPTION_EFFECTIVE_AT = datetime(2026, 9, 2, 0, 10, tzinfo=timezone.utc)
_EFFECTIVE_AT = datetime(2026, 9, 2, 0, 20, tzinfo=timezone.utc)
_RECORDED_AT = datetime(2026, 9, 2, 0, 21, tzinfo=timezone.utc)
_EVIDENCE_REFERENCE = f"urn:cwl:evidence:reconciliation_exception:{_EXCEPTION_ID}:review"
_EVIDENCE_HASH = "sha256:" + "a" * 64
_SOURCE_PAYLOAD_HASH = "sha256:" + "c" * 64
_COMMAND_HASH = "sha256:" + "b" * 64


class _Rows:
    """Small psycopg-result double supporting fetchone."""

    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        """Return the first configured row."""
        return self.rows[0] if self.rows else None


class _Connection:
    """Query-directed transaction double for the resolution command."""

    def __init__(self) -> None:
        self.prior: tuple[object, ...] | None = None
        self.prior_identity: tuple[object, ...] | None = None
        self.run_row: tuple[object, ...] | None = ("evaluating",)
        self.exception_row: tuple[object, ...] | None = (
            "open",
            "urn:cwl:principal:exception_owner",
            _EXCEPTION_EFFECTIVE_AT,
        )
        self.inserted_hash = _COMMAND_HASH
        self.resolution_document: tuple[object, ...] | None = (
            _RESOLUTION_ID,
            "resolved",
            _EVIDENCE_REFERENCE,
            _EVIDENCE_HASH,
            _SOURCE_PAYLOAD_HASH,
            _COMMAND_HASH,
            "urn:cwl:principal:independent_reviewer",
            "bank_reconciliation_exception_review",
            _EFFECTIVE_AT,
            _RECORDED_AT,
        )
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> _Rows:
        """Return configured rows by stable SQL landmarks."""
        normalized = " ".join(query.split())
        self.executed.append((normalized, parameters))
        if normalized.startswith("SET TRANSACTION ISOLATION LEVEL"):
            return _Rows()
        if (
            "FROM accounting_core.reconciliation_exception_resolution_command" in normalized
            and "SELECT reconciliation_run_id" in normalized
        ):
            return _Rows([] if self.prior is None else [self.prior])
        if (
            "SELECT command_family_code" in normalized
            and "FROM accounting_core.reconciliation_command_identity" in normalized
        ):
            return _Rows([] if self.prior_identity is None else [self.prior_identity])
        if (
            "SELECT run_status_code" in normalized
            and "FROM accounting_core.reconciliation_run" in normalized
            and "FOR UPDATE" in normalized
        ):
            return _Rows([] if self.run_row is None else [self.run_row])
        if (
            "SELECT resolution_status_code, owner_reference, effective_at" in normalized
            and "FROM accounting_core.reconciliation_exception" in normalized
        ):
            return _Rows([] if self.exception_row is None else [self.exception_row])
        if normalized.startswith(
            "INSERT INTO accounting_core.reconciliation_exception_resolution_command"
        ):
            return _Rows([(_RESOLUTION_ID, self.inserted_hash, _RECORDED_AT)])
        if normalized.startswith("UPDATE accounting_core.reconciliation_exception"):
            return _Rows()
        if normalized.startswith("INSERT INTO accounting_integration.outbox_event"):
            return _Rows()
        if (
            "FROM accounting_core.reconciliation_exception_resolution_command AS command"
            in normalized
        ):
            return _Rows(
                [] if self.resolution_document is None else [self.resolution_document]
            )
        raise AssertionError(f"unexpected resolution query: {normalized}")


class _Ledger:
    """Tenant-bound ledger double sharing one configured connection."""

    connection = _Connection()
    locks: list[str] = []

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextlib.contextmanager
    def _session(self):
        """Yield one configured transaction."""
        yield type(self).connection

    def _acquire_command_lock(self, _connection: object, scope: str) -> None:
        """Record lock order for concurrency-contract assertions."""
        type(self).locks.append(scope)

    def _require_tenant(self, _connection: object) -> UUID:
        """Return the internal identity for the bound tenant."""
        return _TENANT_ID


def _command(**overrides: object) -> dict[str, object]:
    """Return one canonical exception-resolution command."""
    command: dict[str, object] = {
        "tenant_reference": _TENANT,
        "reconciliation_action_code": "resolve_exception",
        "reconciliation_run_id": str(_RUN_ID),
        "reconciliation_exception_id": str(_EXCEPTION_ID),
        "reconciliation_idempotency_key": "resolve-exception-1",
        "resolution_status_code": "resolved",
        "actor_reference": "urn:cwl:principal:independent_reviewer",
        "purpose_code": "bank_reconciliation_exception_review",
        "resolution_evidence_reference": _EVIDENCE_REFERENCE,
        "resolution_evidence_hash": _EVIDENCE_HASH,
        "effective_at": "2026-09-02T00:20:00Z",
    }
    command.update(overrides)
    return command


def _source_hash(command: dict[str, object] | None = None) -> str:
    """Return the production canonical payload identity for a test command."""
    return resolution._source_payload_hash(_command() if command is None else command)


class ReconciliationExceptionResolutionTests(unittest.TestCase):
    """Exercise validation, maker-checker authority, replay, and atomic write intent."""

    def setUp(self) -> None:
        _Ledger.connection = _Connection()
        _Ledger.locks = []
        self.ledger_patch = mock.patch.object(resolution, "PostgresPostingLedger", _Ledger)
        self.ledger_patch.start()
        self.addCleanup(self.ledger_patch.stop)

    def _resolve(self, command: object | None = None) -> dict[str, object]:
        """Resolve with the fake tenant-bound transaction."""
        return resolution.resolve_reconciliation_exception(
            _command() if command is None else command,
            "postgresql://example",
            _TENANT,
        )

    def test_happy_path_persists_command_status_and_outbox(self) -> None:
        """One valid command writes command evidence before terminal status and outbox."""
        result = self._resolve()

        self.assertEqual(result["resolution_status_code"], "resolved")
        self.assertFalse(result["replayed"])
        self.assertEqual(
            result["reconciliation_exception_resolution_command_hash"], _COMMAND_HASH
        )
        self.assertEqual(
            _Ledger.locks,
            [
                f"reconciliation_run_lifecycle:{_RUN_ID}",
                "reconciliation_exception_resolution_key:resolve-exception-1",
            ],
        )
        sql = [query for query, _parameters in _Ledger.connection.executed]
        command_index = next(
            index
            for index, query in enumerate(sql)
            if query.startswith(
                "INSERT INTO accounting_core.reconciliation_exception_resolution_command"
            )
        )
        status_index = next(
            index
            for index, query in enumerate(sql)
            if query.startswith("UPDATE accounting_core.reconciliation_exception")
        )
        outbox_index = next(
            index
            for index, query in enumerate(sql)
            if query.startswith("INSERT INTO accounting_integration.outbox_event")
        )
        self.assertLess(command_index, status_index)
        self.assertLess(status_index, outbox_index)
        outbox_parameters = _Ledger.connection.executed[outbox_index][1]
        self.assertEqual(outbox_parameters[1], "reconciliation_exception_resolved")
        insert_parameters = _Ledger.connection.executed[command_index][1]
        self.assertEqual(insert_parameters[7], _source_hash())

    def test_superseded_command_uses_supersession_event(self) -> None:
        """Supersession is a distinct terminal decision and event, not a resolution alias."""
        command = _command(resolution_status_code="superseded")
        _Ledger.connection.resolution_document = (
            _RESOLUTION_ID,
            "superseded",
            _EVIDENCE_REFERENCE,
            _EVIDENCE_HASH,
            _source_hash(command),
            _COMMAND_HASH,
            "urn:cwl:principal:independent_reviewer",
            "bank_reconciliation_exception_review",
            _EFFECTIVE_AT,
            _RECORDED_AT,
        )
        result = self._resolve(command)
        self.assertEqual(result["resolution_status_code"], "superseded")
        outbox_parameters = next(
            parameters
            for query, parameters in _Ledger.connection.executed
            if query.startswith("INSERT INTO accounting_integration.outbox_event")
        )
        self.assertEqual(outbox_parameters[1], "reconciliation_exception_superseded")

    def test_exact_replay_returns_immutable_receipt_without_rewriting(self) -> None:
        """An exact idempotency replay returns retained evidence before run-state reads."""
        _Ledger.connection.prior = (
            _RUN_ID,
            _EXCEPTION_ID,
            "resolved",
            _EVIDENCE_REFERENCE,
            _EVIDENCE_HASH,
            _source_hash(),
            "urn:cwl:principal:independent_reviewer",
            "bank_reconciliation_exception_review",
            _EFFECTIVE_AT,
        )
        _Ledger.connection.resolution_document = (
            _RESOLUTION_ID,
            "resolved",
            _EVIDENCE_REFERENCE,
            _EVIDENCE_HASH,
            _source_hash(),
            _COMMAND_HASH,
            "urn:cwl:principal:independent_reviewer",
            "bank_reconciliation_exception_review",
            _EFFECTIVE_AT,
            _RECORDED_AT,
        )
        result = self._resolve()
        self.assertTrue(result["replayed"])
        self.assertEqual(result["source_payload_hash"], _source_hash())
        sql = "\n".join(query for query, _parameters in _Ledger.connection.executed)
        self.assertNotIn("UPDATE accounting_core.reconciliation_exception", sql)
        self.assertNotIn("INSERT INTO accounting_integration.outbox_event", sql)

    def test_changed_replay_conflicts(self) -> None:
        """The same key cannot be rebound to changed resolution evidence."""
        _Ledger.connection.prior = (
            _RUN_ID,
            _EXCEPTION_ID,
            "resolved",
            _EVIDENCE_REFERENCE,
            "sha256:" + "d" * 64,
            _source_hash(),
            "urn:cwl:principal:independent_reviewer",
            "bank_reconciliation_exception_review",
            _EFFECTIVE_AT,
        )
        with self.assertRaises(IdempotencyConflictError):
            self._resolve()

    def test_shared_command_identity_collision_fails_before_run_mutation(self) -> None:
        """Opening, reconciliation, and resolution commands share one idempotency namespace."""
        _Ledger.connection.prior_identity = ("run_opening",)
        with self.assertRaisesRegex(IdempotencyConflictError, "another reconciliation"):
            self._resolve()

    def test_missing_or_terminal_run_fails_closed(self) -> None:
        """Exception decisions remain scoped to an active reviewable run."""
        cases = ((None, "not recorded"), (("reconciled",), "only evaluating"))
        for run_row, message in cases:
            with self.subTest(message=message):
                _Ledger.connection = _Connection()
                with self.assertRaisesRegex(AccountingValidationError, message):
                    self._resolve()

    def test_missing_or_terminal_exception_fails_closed(self) -> None:
        """A command cannot invent an exception or replace its original terminal command."""
        cases = (
            (None, "not recorded"),
            (
                ("resolved", "urn:cwl:principal:exception_owner", _EXCEPTION_EFFECTIVE_AT),
                "already terminal",
            ),
        )
        for exception_row, message in cases:
            with self.subTest(message=message):
                _Ledger.connection = _Connection()
                _Ledger.connection.exception_row = exception_row
                with self.assertRaisesRegex(AccountingValidationError, message):
                    self._resolve()

    def test_maker_checker_and_temporal_causality_fail_closed(self) -> None:
        """The exception owner cannot review itself and resolution cannot predate the exception."""
        _Ledger.connection.exception_row = (
            "open",
            "urn:cwl:principal:independent_reviewer",
            _EXCEPTION_EFFECTIVE_AT,
        )
        with self.assertRaisesRegex(AccountingValidationError, "cannot approve"):
            self._resolve()

        _Ledger.connection = _Connection()
        _Ledger.connection.exception_row = (
            "open",
            "urn:cwl:principal:exception_owner",
            datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(AccountingValidationError, "cannot precede"):
            self._resolve()

    def test_database_must_replace_resolution_hash_sentinel(self) -> None:
        """A missing database hash trigger is never accepted as resolution evidence."""
        _Ledger.connection.inserted_hash = resolution._RESOLUTION_HASH_SENTINEL
        with self.assertRaisesRegex(AccountingValidationError, "hash was not assigned"):
            self._resolve()

    def test_missing_retained_resolution_document_fails_closed(self) -> None:
        """A write or replay cannot fabricate a receipt after command evidence disappears."""
        _Ledger.connection.resolution_document = None
        with self.assertRaisesRegex(AccountingValidationError, "evidence is missing"):
            self._resolve()

    def test_payload_validation_rejects_noncanonical_commands_before_database_work(self) -> None:
        """Shape, tenant, action, identities, references, digest, and time are validated early."""
        cases: tuple[tuple[object, str], ...] = (
            ([], "JSON object"),
            (_command(tenant_reference="urn:cwl:tenant:other"), "tenant_reference"),
            (_command(reconciliation_action_code="reconcile"), "action_code"),
            (_command(reconciliation_run_id="not-a-uuid"), "UUID"),
            (_command(reconciliation_exception_id="not-a-uuid"), "UUID"),
            (_command(reconciliation_idempotency_key=""), "idempotency_key"),
            (_command(resolution_status_code="open"), "resolved or superseded"),
            (_command(actor_reference=" actor"), "actor_reference"),
            (_command(actor_reference="reviewer"), "CWL URN"),
            (_command(purpose_code="Not_Snake"), "lower snake_case"),
            (_command(resolution_evidence_reference="evidence"), "CWL URN"),
            (_command(resolution_evidence_hash="sha256:short"), "canonical sha256"),
            (_command(effective_at="2026-09-02 00:20:00"), "canonical UTC"),
            (_command(request_context={"bad": {1, 2}}), "JSON-compatible"),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                _Ledger.connection = _Connection()
                with self.assertRaisesRegex(AccountingValidationError, message):
                    self._resolve(payload)
                self.assertEqual(_Ledger.connection.executed, [])


if __name__ == "__main__":
    unittest.main()
