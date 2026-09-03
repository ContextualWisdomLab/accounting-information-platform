"""RED proof that lifecycle authority cannot reuse a pre-lock repeatable-read snapshot."""

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


class ReconciliationLifecyclePrelockSnapshotRedTests(unittest.TestCase):
    """Require lifecycle authority to start its snapshot after the session-lock grant."""

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
        """Resolve the database tenant identity for the opened reconciliation run."""
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
        """Attempt the raw transition table path after caller-managed advisory locks."""
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
                "prelock-snapshot-" + self.opened["reconciliation_run_id"],
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                "sha256:" + "c" * 64,
                "sha256:" + "d" * 64,
                "sha256:" + "0" * 64,
                "urn:cwl:principal:direct_database_test",
                "month_end_reconciliation",
                "2026-09-03T00:30:00Z",
            ),
        )

    def test_repeatable_read_snapshot_created_before_session_lock_cannot_finalize(self) -> None:
        """A pre-lock snapshot cannot become reconciliation authority after a competing commit."""
        lifecycle_scope = (
            "reconciliation_run_lifecycle:" + self.opened["reconciliation_run_id"]
        )
        snapshot_ready = Event()
        lock_requested = Event()
        worker_pid: dict[str, int] = {}
        worker_error: list[BaseException] = []

        with psycopg.connect(posting.DATABASE_URL) as writer:
            tenant_id = self._tenant_id(writer)
            writer_pid = writer.execute("SELECT pg_backend_pid()").fetchone()[0]
            writer.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (self.fixture.case.policy.tenant_reference, lifecycle_scope),
            )

            def attempt_from_stale_snapshot() -> None:
                try:
                    with psycopg.connect(posting.DATABASE_URL) as connection:
                        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                        worker_pid["value"] = connection.execute(
                            "SELECT pg_backend_pid()"
                        ).fetchone()[0]
                        # This first query fixes snapshot S0 before the lifecycle
                        # session lock is requested.
                        connection.execute("SELECT 1").fetchone()
                        snapshot_ready.set()
                        lock_requested.set()
                        connection.execute(
                            "SELECT pg_advisory_lock(hashtext(%s), hashtext(%s))",
                            (self.fixture.case.policy.tenant_reference, lifecycle_scope),
                        )
                        connection.execute(
                            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                            (self.fixture.case.policy.tenant_reference, lifecycle_scope),
                        )
                        try:
                            self._raw_transition(connection, tenant_id)
                            connection.commit()
                        finally:
                            # A rejected transition leaves the transaction aborted.
                            # Clear that state before releasing the session advisory lock
                            # so cleanup cannot mask the authority error under test.
                            connection.rollback()
                            connection.execute(
                                "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                                (self.fixture.case.policy.tenant_reference, lifecycle_scope),
                            )
                            connection.commit()
                except BaseException as error:  # pragma: no cover - asserted below
                    worker_error.append(error)

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(attempt_from_stale_snapshot)
                self.assertTrue(snapshot_ready.wait(timeout=10))

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
                    VALUES (%s, %s, 'post_snapshot_exception',
                            'urn:cwl:principal:controller_owner',
                            'Review this exception before reconciliation.', %s, 'open')
                    """,
                    (
                        tenant_id,
                        self.opened["reconciliation_run_id"],
                        datetime(2026, 9, 3, 0, 20, tzinfo=timezone.utc),
                    ),
                )
                self.assertTrue(lock_requested.wait(timeout=10))

                deadline = time.monotonic() + 5
                blocked = False
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as monitor:
                    while time.monotonic() < deadline and not future.done():
                        blockers = monitor.execute(
                            "SELECT pg_blocking_pids(%s)",
                            (worker_pid["value"],),
                        ).fetchone()[0]
                        if writer_pid in blockers:
                            blocked = True
                            break
                        time.sleep(0.05)
                self.assertTrue(blocked, "raw lifecycle caller never waited for the lifecycle lock")

                writer.commit()
                future.result(timeout=10)

        # GREEN requires an explicit database boundary rejecting a transaction
        # whose authority snapshot predates the session-lock acquisition step.
        self.assertEqual(len(worker_error), 1)
        self.assertIsInstance(worker_error[0], psycopg.Error)
        self.assertIn(
            "reconciliation_lifecycle_fresh_transaction_required",
            str(worker_error[0]),
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            status = connection.execute(
                """
                SELECT run_status_code
                FROM accounting_core.reconciliation_run
                WHERE reconciliation_run_id = %s
                """,
                (self.opened["reconciliation_run_id"],),
            ).fetchone()[0]
            transition_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.reconciliation_run_transition_command
                WHERE reconciliation_run_id = %s
                  AND reconciliation_transition_idempotency_key = %s
                """,
                (
                    self.opened["reconciliation_run_id"],
                    "prelock-snapshot-" + self.opened["reconciliation_run_id"],
                ),
            ).fetchone()[0]
        self.assertNotEqual(status, "reconciled")
        self.assertEqual(transition_count, 0)


if __name__ == "__main__":
    unittest.main()
