"""Contracts for evidence-derived reconciliation-run lifecycle transitions."""

from __future__ import annotations

import contextlib
import unittest
import unittest.mock as mock
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from accounting_information_platform import AccountingValidationError, IdempotencyConflictError
from accounting_information_platform import reconciliation_lifecycle as lifecycle
from accounting_information_platform import reconciliation_close_package as close_package

_TENANT = "urn:cwl:tenant:test"
_RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
_TRANSITION_ID = UUID("22222222-2222-2222-2222-222222222222")
_EFFECTIVE_AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
_RECORDED_AT = datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc)
_COMMAND_HASH = "sha256:" + "a" * 64
_TRANSITION_HASH = "sha256:" + "b" * 64
_SNAPSHOT_HASH = "sha256:" + "c" * 64
_STATEMENT_POPULATION_HASH = "sha256:" + "1" * 64
_BOOK_POPULATION_HASH = "sha256:" + "2" * 64


class _Rows:
    """Small psycopg-result double supporting fetchone/fetchall."""

    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        """Return the first configured row."""
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return all configured rows."""
        return list(self.rows)


class _Connection:
    """Query-directed connection double for lifecycle command behavior."""

    def __init__(self) -> None:
        self.prior_transition: tuple[object, ...] | None = None
        self.open_key_collision = False
        self.run_status: str | None = "evaluating"
        self.existing_transition_key: str | None = None
        self.match_rows: list[tuple[object, ...]] = []
        self.exception_rows: list[tuple[object, ...]] = []
        self.run_command_hash: str | None = _COMMAND_HASH
        self.transition_document: tuple[object, ...] | None = (
            _TRANSITION_ID,
            _SNAPSHOT_HASH,
            _TRANSITION_HASH,
            "urn:cwl:principal:controller",
            "month_end_reconciliation",
            _EFFECTIVE_AT,
            _RECORDED_AT,
            "reconciled",
            _STATEMENT_POPULATION_HASH,
            _BOOK_POPULATION_HASH,
        )
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def execute(
        self, query: str, parameters: tuple[object, ...] = ()
    ) -> _Rows:
        """Return fixture rows by stable SQL landmarks."""
        normalized = " ".join(query.split())
        self.executed.append((normalized, parameters))
        if normalized.startswith("SET TRANSACTION ISOLATION LEVEL"):
            return _Rows()
        if (
            "FROM accounting_core.reconciliation_run_transition_command" in normalized
            and "SELECT reconciliation_run_id, actor_reference" in normalized
        ):
            return _Rows([] if self.prior_transition is None else [self.prior_transition])
        if (
            "FROM accounting_core.reconciliation_run_command" in normalized
            and "SELECT 1" in normalized
        ):
            return _Rows([(1,)] if self.open_key_collision else [])
        if (
            "SELECT run_status_code" in normalized
            and "FROM accounting_core.reconciliation_run" in normalized
            and "FOR UPDATE" in normalized
        ):
            return _Rows([] if self.run_status is None else [(self.run_status, "KRW")])
        if (
            "SELECT reconciliation_transition_idempotency_key" in normalized
            and "FROM accounting_core.reconciliation_run_transition_command" in normalized
        ):
            return _Rows(
                [] if self.existing_transition_key is None else [(self.existing_transition_key,)]
            )
        if "FROM accounting_core.reconciliation_match AS reviewed_match" in normalized:
            return _Rows(self.match_rows)
        if "FROM accounting_core.reconciliation_exception" in normalized:
            return _Rows(self.exception_rows)
        if (
            "SELECT reconciliation_command_hash" in normalized
            and "FROM accounting_core.reconciliation_run_command" in normalized
        ):
            return _Rows(
                [] if self.run_command_hash is None else [(self.run_command_hash,)]
            )
        if normalized.startswith(
            "INSERT INTO accounting_core.reconciliation_run_transition_command"
        ):
            return _Rows([(_TRANSITION_ID, _TRANSITION_HASH, _RECORDED_AT)])
        if normalized.startswith("UPDATE accounting_core.reconciliation_run"):
            self.run_status = "reconciled"
            return _Rows()
        if normalized.startswith("INSERT INTO accounting_integration.outbox_event"):
            return _Rows()
        if (
            "FROM accounting_core.reconciliation_run_transition_command AS transition" in normalized
            and "JOIN accounting_core.reconciliation_run AS run" in normalized
        ):
            return _Rows(
                [] if self.transition_document is None else [self.transition_document]
            )
        raise AssertionError(f"unexpected lifecycle query: {normalized}")


class _Ledger:
    """Tenant-bound ledger double sharing one configured connection."""

    connection = _Connection()
    locks: list[str] = []

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextlib.contextmanager
    def _session(self):
        """Yield the configured transaction connection."""
        yield type(self).connection

    def _acquire_command_lock(self, _connection: object, scope: str) -> None:
        """Record the command lock order."""
        type(self).locks.append(scope)

    def _require_tenant(self, _connection: object) -> UUID:
        """Return the internal identity for the bound test tenant."""
        return UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _bridge() -> SimpleNamespace:
    """Return one exact bridge that can support a reconciled lifecycle state."""
    return SimpleNamespace(
        reconciliation_run_reference=str(_RUN_ID),
        statement_population_reference=_STATEMENT_POPULATION_HASH,
        book_population_reference=_BOOK_POPULATION_HASH,
        currency_code="KRW",
        statement_opening_balance=100000,
        statement_period_movements=15000,
        statement_closing_balance=115000,
        book_opening_balance=100000,
        posted_cash_book_movements=0,
        book_closing_balance=100000,
        reconciled_book_balance=100000,
        outstanding_bank_items=0,
        outstanding_book_items=15000,
        unexplained_difference=0,
        status_code="reconciled",
    )


def _command(**overrides: object) -> dict[str, object]:
    """Return one canonical reconcile command with optional field overrides."""
    command: dict[str, object] = {
        "tenant_reference": _TENANT,
        "reconciliation_action_code": "reconcile",
        "reconciliation_run_id": str(_RUN_ID),
        "reconciliation_idempotency_key": "reconcile-run-1",
        "actor_reference": "urn:cwl:principal:controller",
        "purpose_code": "month_end_reconciliation",
        "effective_at": "2026-09-01T12:00:00Z",
    }
    command.update(overrides)
    return command


class ReconciliationLifecycleTests(unittest.TestCase):
    """Exercise command validation, review state, bridge authority, and replay."""

    def setUp(self) -> None:
        _Ledger.connection = _Connection()
        _Ledger.locks = []
        self.ledger_patch = mock.patch.object(lifecycle, "PostgresPostingLedger", _Ledger)
        self.bridge_patch = mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            return_value=_bridge(),
        )
        self.ledger_patch.start()
        self.bridge_mock = self.bridge_patch.start()
        self.addCleanup(self.ledger_patch.stop)
        self.addCleanup(self.bridge_patch.stop)

    def _reconcile(self, command: object | None = None) -> dict[str, object]:
        return lifecycle.reconcile_reconciliation_run(
            _command() if command is None else command,
            "postgresql://example",
            _TENANT,
        )

    def test_happy_path_binds_bridge_review_state_and_outbox(self) -> None:
        """A reviewed exact bridge yields one immutable reconciliation receipt."""
        _Ledger.connection.match_rows = [
            ("match-a", "approved", "approved", "sha256:" + "3" * 64),
            ("match-b", "rejected", "rejected", "sha256:" + "4" * 64),
            ("match-c", "superseded", "approved", "sha256:" + "5" * 64),
        ]
        result = self._reconcile()

        self.assertEqual(result["run_status_code"], "reconciled")
        self.assertEqual(result["reconciliation_transition_id"], str(_TRANSITION_ID))
        self.assertRegex(str(result["reconciliation_snapshot_hash"]), r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(result["statement_population_reference"], _STATEMENT_POPULATION_HASH)
        self.assertEqual(result["book_population_reference"], _BOOK_POPULATION_HASH)
        self.assertFalse(result["replayed"])
        self.assertEqual(
            _Ledger.locks,
            [
                f"reconciliation_run_lifecycle:{_RUN_ID}",
                "reconciliation_run_transition_key:reconcile-run-1",
            ],
        )
        self.bridge_mock.assert_called_once()
        sql = "\n".join(query for query, _parameters in _Ledger.connection.executed)
        self.assertIn("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", sql)
        self.assertIn("INSERT INTO accounting_integration.outbox_event", sql)
        self.assertIn("UPDATE accounting_core.reconciliation_run", sql)

    def test_exact_transition_replays_without_rebuilding_bridge(self) -> None:
        """An exact lifecycle key replays the durable transition receipt."""
        _Ledger.connection.prior_transition = (
            _RUN_ID,
            "urn:cwl:principal:controller",
            "month_end_reconciliation",
            _EFFECTIVE_AT,
        )
        result = self._reconcile()
        self.assertTrue(result["replayed"])
        self.assertEqual(result["reconciliation_transition_command_hash"], _TRANSITION_HASH)
        self.bridge_mock.assert_not_called()

    def test_reused_transition_key_rejects_each_changed_identity_field(self) -> None:
        """Run, actor, purpose, or effective-time changes under one key conflict."""
        cases = (
            (
                (UUID("33333333-3333-3333-3333-333333333333"), "urn:cwl:principal:controller", "month_end_reconciliation", _EFFECTIVE_AT),
                "run",
            ),
            ((_RUN_ID, "urn:cwl:principal:other", "month_end_reconciliation", _EFFECTIVE_AT), "actor"),
            ((_RUN_ID, "urn:cwl:principal:controller", "other_purpose", _EFFECTIVE_AT), "purpose"),
            ((_RUN_ID, "urn:cwl:principal:controller", "month_end_reconciliation", datetime(2026, 9, 1, 12, 2, tzinfo=timezone.utc)), "effective"),
        )
        for prior, label in cases:
            with self.subTest(label=label):
                _Ledger.connection = _Connection()
                _Ledger.connection.prior_transition = prior
                with self.assertRaises(IdempotencyConflictError):
                    self._reconcile()

    def test_opening_command_key_cannot_be_reused_for_transition(self) -> None:
        """Opening and lifecycle commands occupy distinct command identities."""
        _Ledger.connection.open_key_collision = True
        with self.assertRaisesRegex(IdempotencyConflictError, "run-opening"):
            self._reconcile()

    def test_missing_run_fails_before_bridge(self) -> None:
        """A transition cannot invent a reconciliation run."""
        _Ledger.connection.run_status = None
        with self.assertRaisesRegex(AccountingValidationError, "not recorded"):
            self._reconcile()
        self.bridge_mock.assert_not_called()

    def test_legacy_reconciled_status_without_transition_evidence_fails_closed(self) -> None:
        """A direct historical status update cannot masquerade as lifecycle authority."""
        _Ledger.connection.run_status = "reconciled"
        with self.assertRaisesRegex(AccountingValidationError, "without durable"):
            self._reconcile()

    def test_reconciled_run_requires_replay_of_original_key(self) -> None:
        """A second key cannot create another reconcile transition for one run."""
        _Ledger.connection.run_status = "reconciled"
        _Ledger.connection.existing_transition_key = "original-key"
        with self.assertRaisesRegex(IdempotencyConflictError, "original-key"):
            self._reconcile()

    def test_terminal_nonreviewable_status_fails_closed(self) -> None:
        """Not-reconciled or superseded runs cannot be promoted by this command."""
        for status in ("not_reconciled", "superseded"):
            with self.subTest(status=status):
                _Ledger.connection = _Connection()
                _Ledger.connection.run_status = status
                with self.assertRaisesRegex(AccountingValidationError, "only evaluating"):
                    self._reconcile()

    def test_unreviewed_and_inconsistent_match_evidence_fails_closed(self) -> None:
        """Every active reviewed match must have a terminal decision-consistent snapshot."""
        cases = (
            ([("match-a", "proposed", "", "")], "still requires review"),
            ([("match-a", "approved", "", "")], "decision-consistent"),
            ([("match-a", "rejected", "approved", "sha256:" + "1" * 64)], "decision-consistent"),
        )
        for rows, message in cases:
            with self.subTest(message=message):
                _Ledger.connection = _Connection()
                _Ledger.connection.match_rows = rows
                with self.assertRaisesRegex(AccountingValidationError, message):
                    self._reconcile()

    def test_open_exception_blocks_reconciliation(self) -> None:
        """Unresolved exception evidence requires an operator action before finalization."""
        _Ledger.connection.exception_rows = [
            ("exception-a", "amount_mismatch", "open")
        ]
        with self.assertRaisesRegex(AccountingValidationError, "still open"):
            self._reconcile()

    def test_terminal_exception_without_resolution_command_blocks_reconciliation(self) -> None:
        """Mutable terminal status is not sufficient maker-checker authority."""
        for status in ("resolved", "superseded"):
            with self.subTest(status=status):
                _Ledger.connection = _Connection()
                _Ledger.connection.exception_rows = [
                    ("exception-a", "amount_mismatch", status)
                ]
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "resolution-command evidence",
                ):
                    self._reconcile()

    def test_non_tying_bridge_is_actionable_validation_failure(self) -> None:
        """The lifecycle API converts exact-bridge failures to buyer-actionable validation."""
        self.bridge_mock.side_effect = ValueError("database-owned bridge does not tie")
        with self.assertRaisesRegex(AccountingValidationError, "does not tie exactly"):
            self._reconcile()

    def test_missing_opening_command_provenance_fails_closed(self) -> None:
        """Finalization requires the immutable command that opened the run."""
        _Ledger.connection.run_command_hash = None
        with self.assertRaisesRegex(AccountingValidationError, "opening-command"):
            self._reconcile()

    def test_transition_document_missing_is_not_success_shaped(self) -> None:
        """Replay cannot fabricate a receipt when transition evidence disappeared."""
        _Ledger.connection.prior_transition = (
            _RUN_ID,
            "urn:cwl:principal:controller",
            "month_end_reconciliation",
            _EFFECTIVE_AT,
        )
        _Ledger.connection.transition_document = None
        with self.assertRaisesRegex(AccountingValidationError, "evidence is missing"):
            self._reconcile()

    def test_payload_validation_fails_before_database_work(self) -> None:
        """Shape, tenant, action, identifiers, and timestamps fail before a transaction."""
        cases: tuple[tuple[object, str], ...] = (
            ([], "JSON object"),
            (_command(tenant_reference="urn:cwl:tenant:other"), "tenant_reference"),
            (_command(reconciliation_action_code="close"), "action_code"),
            (_command(reconciliation_idempotency_key=""), "idempotency_key"),
            (_command(actor_reference=" actor"), "actor_reference"),
            (_command(actor_reference="controller"), "CWL URN"),
            (_command(purpose_code=""), "purpose_code"),
            (_command(reconciliation_run_id="not-a-uuid"), "UUID"),
            (_command(effective_at="2026-09-01 12:00:00"), "canonical UTC"),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                _Ledger.connection = _Connection()
                with self.assertRaisesRegex(AccountingValidationError, message):
                    self._reconcile(payload)
                self.assertEqual(_Ledger.connection.executed, [])

    def test_snapshot_hash_changes_with_authoritative_control_state(self) -> None:
        """The transition digest binds source populations and reviewed state."""
        baseline = lifecycle._transition_snapshot_hash(
            run_id=_RUN_ID,
            run_command_hash=_COMMAND_HASH,
            bridge=_bridge(),
            match_state=(),
            exception_state=(),
        )
        changed = lifecycle._transition_snapshot_hash(
            run_id=_RUN_ID,
            run_command_hash=_COMMAND_HASH,
            bridge=_bridge(),
            match_state=(("match-a", "approved", "approved", "sha256:" + "4" * 64),),
            exception_state=(),
        )
        self.assertRegex(baseline, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(baseline, changed)


class ReconciliationLifecycleMigrationContractTests(unittest.TestCase):
    """Keep database status authority and lifecycle serialization in migration 0019."""

    def test_migration_persists_transition_and_rejects_direct_reconciled_status(self) -> None:
        """The unreleased migration carries immutable transition, lock, and status guards."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "database/migrations/0019_reconciliation_run_command_evidence.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE accounting_core.reconciliation_run_transition_command", migration)
        self.assertIn("reconciliation_lifecycle_command_required", migration)
        self.assertIn("acquire_reconciliation_run_lifecycle_lock", migration)
        self.assertIn("reconciliation_lifecycle_frozen", migration)
        self.assertIn("reconciliation_match_snapshot_hash", migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)


if __name__ == "__main__":
    unittest.main()
