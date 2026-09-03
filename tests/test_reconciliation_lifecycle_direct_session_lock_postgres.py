"""Real PostgreSQL proof that raw lifecycle authority requires a pre-statement session lock."""

from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleDirectSessionLockPostgresTests(unittest.TestCase):
    """Reject raw transition statements that did not enter the fresh-snapshot protocol."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating run over retained statement evidence."""
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

    def _tenant_id(self, connection: psycopg.Connection) -> object:
        """Resolve the internal tenant identity for this run."""
        row = connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()
        assert row is not None
        return row[0]

    def _raw_transition(self, connection: psycopg.Connection, tenant_id: object) -> None:
        """Attempt the raw table path without the supported application lock protocol."""
        connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run_transition_command (
                tenant_account_id,
                reconciliation_run_id,
                reconciliation_transition_idempotency_key,
                target_run_status_code,
                reconciliation_snapshot_hash,
                statement_population_reference,
                book_population_reference,
                source_payload_hash,
                reconciliation_transition_command_hash,
                actor_reference,
                purpose_code,
                effective_at
            )
            VALUES (%s, %s, %s, 'reconciled', %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                self.opened["reconciliation_run_id"],
                "direct-session-lock-" + self.opened["reconciliation_run_id"],
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                "sha256:" + "c" * 64,
                "sha256:" + "d" * 64,
                "sha256:" + "0" * 64,
                "urn:cwl:principal:direct_database_test",
                "month_end_reconciliation",
                "2026-09-02T00:30:00Z",
            ),
        )

    def _assert_raw_transition_rejected_after_predecessor_lock_wait(
        self, isolation_level: str
    ) -> None:
        """Prove a raw statement cannot resume from a snapshot created before the lock grant."""
        lifecycle_scope = (
            "reconciliation_run_lifecycle:" + self.opened["reconciliation_run_id"]
        )
        worker_started = Event()
        worker_pid: dict[str, int] = {}
        worker_error: list[BaseException] = []

        with psycopg.connect(posting.DATABASE_URL) as writer:
            tenant_id = self._tenant_id(writer)
            writer_pid = writer.execute("SELECT pg_backend_pid()").fetchone()[0]
            writer.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (self.fixture.case.policy.tenant_reference, lifecycle_scope),
            )
            writer.execute(
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
                VALUES (%s, %s, 'late_direct_sql_exception',
                        'urn:cwl:principal:controller_owner',
                        'Review this exception before reconciliation.', %s, 'open')
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    datetime(2026, 9, 2, 0, 20, tzinfo=timezone.utc),
                ),
            )

            def attempt_raw_transition() -> None:
                try:
                    with psycopg.connect(posting.DATABASE_URL) as connection:
                        if isolation_level == "REPEATABLE READ":
                            connection.execute(
                                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                            )
                        worker_pid["value"] = connection.execute(
                            "SELECT pg_backend_pid()"
                        ).fetchone()[0]
                        worker_started.set()
                        self._raw_transition(connection, tenant_id)
                        connection.rollback()
                except BaseException as error:  # pragma: no cover - asserted below
                    worker_error.append(error)

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(attempt_raw_transition)
                self.assertTrue(worker_started.wait(timeout=10))

                blocked = False
                deadline = time.monotonic() + 5
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as monitor:
                    while time.monotonic() < deadline and not future.done():
                        blockers = monitor.execute(
                            "SELECT pg_blocking_pids(%s)",
                            (worker_pid["value"],),
                        ).fetchone()[0]
                        if writer_pid in blockers:
                            blocked = True
                            break
                        time.sleep(0.02)

                if blocked:
                    writer.commit()
                else:
                    writer.rollback()
                future.result(timeout=10)

        self.assertEqual(len(worker_error), 1)
        self.assertIsInstance(worker_error[0], psycopg.Error)
        self.assertIn(
            "reconciliation_lifecycle_session_lock_required",
            str(worker_error[0]),
        )

    def test_read_committed_raw_transition_requires_pre_statement_session_lock(self) -> None:
        """READ COMMITTED raw DML cannot wait after deriving a predecessor statement snapshot."""
        self._assert_raw_transition_rejected_after_predecessor_lock_wait("READ COMMITTED")

    def test_repeatable_read_raw_transition_requires_pre_statement_session_lock(self) -> None:
        """REPEATABLE READ raw DML cannot retain a pre-lock transaction snapshot."""
        self._assert_raw_transition_rejected_after_predecessor_lock_wait("REPEATABLE READ")


if __name__ == "__main__":
    unittest.main()
