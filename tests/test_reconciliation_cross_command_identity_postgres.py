"""Real PostgreSQL regressions for the shared reconciliation command namespace."""

from __future__ import annotations

import threading
import time
import unittest
import uuid
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationCrossCommandIdentityPostgresTests(unittest.TestCase):
    """Prove opening and lifecycle commands cannot claim one tenant/key twice."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete migration chain in real PostgreSQL."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one authoritative run whose opening key is already durable."""
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
        """Resolve the internal tenant identity for this test aggregate."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def test_transition_cannot_reuse_opening_command_key(self) -> None:
        """The database, not a prior application SELECT, owns cross-family uniqueness."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            with self.assertRaisesRegex(psycopg.Error, "idempotency key"):
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
                        reconciliation_transition_command_hash,
                        actor_reference,
                        purpose_code,
                        effective_at
                    )
                    VALUES (
                        %s, %s, %s, 'reconciled', %s, %s, %s, %s,
                        'urn:cwl:principal:cross_command_test',
                        'month_end_reconciliation', %s
                    )
                    """,
                    (
                        tenant_id,
                        self.opened["reconciliation_run_id"],
                        self.opened["reconciliation_idempotency_key"],
                        "sha256:" + "a" * 64,
                        "sha256:" + "b" * 64,
                        "sha256:" + "c" * 64,
                        "sha256:" + "0" * 64,
                        datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc),
                    ),
                )
            connection.rollback()

    def test_shared_identity_serializes_concurrent_claims(self) -> None:
        """Concurrent transactions cannot materialize two identities for one tenant/key."""
        key = f"concurrent-shared-{uuid.uuid4().hex}"
        first_inserted = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        failures: list[BaseException] = []
        second_sqlstate: list[str | None] = []

        with psycopg.connect(posting.DATABASE_URL) as lookup:
            tenant_id = self._tenant_id(lookup)

        def first_claim() -> None:
            try:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    connection.execute(
                        """
                        INSERT INTO accounting_core.reconciliation_command_identity (
                            tenant_account_id,
                            reconciliation_idempotency_key,
                            reconciliation_command_kind_code,
                            reconciliation_run_id
                        )
                        VALUES (%s, %s, 'run_open', %s)
                        """,
                        (tenant_id, key, self.opened["reconciliation_run_id"]),
                    )
                    first_inserted.set()
                    if not release_first.wait(timeout=5):
                        raise AssertionError("concurrent identity test did not release first writer")
                    connection.commit()
            except BaseException as error:  # captured for the main test thread
                failures.append(error)
                first_inserted.set()

        def second_claim() -> None:
            if not first_inserted.wait(timeout=5):
                failures.append(AssertionError("first identity writer did not reach PostgreSQL"))
                return
            try:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    second_started.set()
                    connection.execute(
                        """
                        INSERT INTO accounting_core.reconciliation_command_identity (
                            tenant_account_id,
                            reconciliation_idempotency_key,
                            reconciliation_command_kind_code,
                            reconciliation_run_id
                        )
                        VALUES (%s, %s, 'run_reconcile', %s)
                        """,
                        (tenant_id, key, self.opened["reconciliation_run_id"]),
                    )
                    connection.commit()
            except psycopg.Error as error:
                second_sqlstate.append(error.sqlstate)
            except BaseException as error:  # captured for the main test thread
                failures.append(error)

        first = threading.Thread(target=first_claim, name="reconciliation-key-first")
        second = threading.Thread(target=second_claim, name="reconciliation-key-second")
        first.start()
        second.start()
        self.assertTrue(first_inserted.wait(timeout=5))
        self.assertTrue(second_started.wait(timeout=5))
        time.sleep(0.1)
        self.assertTrue(
            second.is_alive(),
            "second identity claim must wait on the first uncommitted unique key",
        )
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(second_sqlstate, ["23505"])

        with psycopg.connect(posting.DATABASE_URL) as connection:
            count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.reconciliation_command_identity
                WHERE tenant_account_id = %s
                  AND reconciliation_idempotency_key = %s
                """,
                (tenant_id, key),
            ).fetchone()[0]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
