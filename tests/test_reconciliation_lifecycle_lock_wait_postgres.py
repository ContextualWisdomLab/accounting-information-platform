"""Real PostgreSQL visibility test for reconciliation lifecycle advisory-lock waits."""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event, Thread, current_thread
from types import SimpleNamespace
import uuid
from unittest import mock

import psycopg

from accounting_information_platform import (
    accept_reconciliation_run,
    reconcile_reconciliation_run,
    resolve_reconciliation_exception,
)
from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform.persistence import PostgresPostingLedger
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleLockWaitPostgresTests(unittest.TestCase):
    """Prove finalization sees evidence committed by the lifecycle-lock predecessor."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the shared PostgreSQL migration chain used by reconciliation controls."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating run with one unresolved maker-owned exception."""
        self.fixture = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            self.exception_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception (
                    tenant_account_id,
                    reconciliation_run_id,
                    exception_code,
                    owner_reference,
                    next_action,
                    effective_at,
                    resolution_status_code
                )
                VALUES (
                    %s, %s, 'lock_wait_visibility',
                    'urn:cwl:principal:exception_maker',
                    'Complete maker-checker review, then finalize this reconciliation run.',
                    %s, 'open'
                )
                RETURNING reconciliation_exception_id
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    datetime(2026, 9, 2, 0, 10, tzinfo=timezone.utc),
                ),
            ).fetchone()[0]
            connection.commit()

    def _tenant_id(self, connection: psycopg.Connection) -> object:
        """Resolve the database tenant identity for the opened reconciliation run."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def _resolution_command(self) -> dict[str, object]:
        """Return one independent-reviewer exception-resolution command."""
        return {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "resolve_exception",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_exception_id": str(self.exception_id),
            "reconciliation_idempotency_key": f"resolve-wait-{self.exception_id}",
            "resolution_status_code": "resolved",
            "actor_reference": "urn:cwl:principal:independent_reviewer",
            "purpose_code": "bank_reconciliation_exception_review",
            "resolution_evidence_reference": (
                f"urn:cwl:evidence:reconciliation_exception:{self.exception_id}:review"
            ),
            "resolution_evidence_hash": "sha256:" + "a" * 64,
            "effective_at": "2026-09-02T00:20:00Z",
        }

    def _lifecycle_command(self) -> dict[str, object]:
        """Return one purpose-bound reconciliation finalization command."""
        return {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_idempotency_key": f"reconcile-wait-{uuid.uuid4().hex}",
            "actor_reference": "urn:cwl:principal:reconciliation_controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2026-09-02T00:30:00Z",
        }

    def _bridge(self) -> SimpleNamespace:
        """Return a tied bridge so the test isolates lock-wait evidence visibility."""
        return SimpleNamespace(
            reconciliation_run_reference=self.opened["reconciliation_run_id"],
            statement_population_reference="sha256:" + "1" * 64,
            book_population_reference="sha256:" + "2" * 64,
            statement_opening_balance=Decimal("100000.00"),
            statement_period_movements=Decimal("15000.00"),
            statement_closing_balance=Decimal("115000.00"),
            book_opening_balance=Decimal("100000.00"),
            posted_cash_book_movements=Decimal("0.00"),
            book_closing_balance=Decimal("100000.00"),
            reconciled_book_balance=Decimal("100000.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("15000.00"),
            unexplained_difference=Decimal("0.00"),
            status_code="reconciled",
        )

    def test_waiting_finalizer_observes_resolution_committed_before_lock_grant(self) -> None:
        """A lock waiter must evaluate the committed resolution, not its pre-wait snapshot."""
        lifecycle_scope = (
            "reconciliation_run_lifecycle:" + self.opened["reconciliation_run_id"]
        )
        writer_holds_lock = Event()
        release_writer = Event()
        finalizer_started_lock_statement = Event()
        finalizer_pid: list[int] = []
        outcomes: dict[str, dict[str, object]] = {}
        failures: list[BaseException] = []
        original_lock = PostgresPostingLedger._acquire_command_lock

        def gated_lock(
            ledger: PostgresPostingLedger, connection: object, scope: str
        ) -> None:
            if scope == lifecycle_scope and current_thread().name == "lifecycle-finalizer":
                pid = int(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
                finalizer_pid.append(pid)
                finalizer_started_lock_statement.set()
                original_lock(ledger, connection, scope)
                return
            original_lock(ledger, connection, scope)
            if scope == lifecycle_scope and current_thread().name == "resolution-writer":
                writer_holds_lock.set()
                if not release_writer.wait(timeout=10):
                    raise TimeoutError("test did not release the resolution writer")

        def run_resolution() -> None:
            try:
                outcomes["resolution"] = resolve_reconciliation_exception(
                    self._resolution_command(),
                    posting.DATABASE_URL,
                    self.fixture.case.policy.tenant_reference,
                )
            except BaseException as error:  # pragma: no cover - surfaced below
                failures.append(error)

        def run_finalization() -> None:
            try:
                outcomes["finalization"] = reconcile_reconciliation_run(
                    self._lifecycle_command(),
                    posting.DATABASE_URL,
                    self.fixture.case.policy.tenant_reference,
                )
            except BaseException as error:  # pragma: no cover - surfaced below
                failures.append(error)

        with mock.patch.object(
            PostgresPostingLedger,
            "_acquire_command_lock",
            gated_lock,
        ), mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            side_effect=lambda *_args, **_kwargs: self._bridge(),
        ):
            writer = Thread(target=run_resolution, name="resolution-writer")
            finalizer = Thread(target=run_finalization, name="lifecycle-finalizer")
            writer.start()
            self.assertTrue(writer_holds_lock.wait(timeout=10))
            finalizer.start()
            try:
                self.assertTrue(finalizer_started_lock_statement.wait(timeout=10))
                waiting = False
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with psycopg.connect(posting.DATABASE_URL) as monitor:
                        waiting = bool(
                            monitor.execute(
                                """
                                SELECT EXISTS (
                                    SELECT 1
                                    FROM pg_locks
                                    WHERE pid = %s
                                      AND locktype = 'advisory'
                                      AND NOT granted
                                )
                                """,
                                (finalizer_pid[0],),
                            ).fetchone()[0]
                        )
                    if waiting:
                        break
                    time.sleep(0.05)
                self.assertTrue(
                    waiting,
                    "finalizer never became an advisory-lock waiter",
                )
            finally:
                release_writer.set()
            writer.join(timeout=15)
            finalizer.join(timeout=15)

        self.assertFalse(writer.is_alive())
        self.assertFalse(finalizer.is_alive())
        if failures:
            self.fail(f"concurrent reconciliation control failed: {failures!r}")
        self.assertEqual(outcomes["resolution"]["resolution_status_code"], "resolved")
        self.assertEqual(outcomes["finalization"]["run_status_code"], "reconciled")


if __name__ == "__main__":
    unittest.main()
