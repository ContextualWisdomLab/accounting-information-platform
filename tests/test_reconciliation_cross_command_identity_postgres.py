"""Real PostgreSQL regressions for the shared reconciliation command namespace."""

from __future__ import annotations

import threading
import unittest
import uuid
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import IdempotencyConflictError, accept_reconciliation_run
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

    def test_opening_api_reports_lifecycle_owned_key_as_domain_conflict(self) -> None:
        """A durable lifecycle key never leaks a provider-specific unique violation."""
        key = f"lifecycle-owned-{uuid.uuid4().hex}"
        _statement, command = self.fixture._statement_and_command()
        command["reconciliation_idempotency_key"] = key
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_command_identity (
                    tenant_account_id,
                    reconciliation_command_identity_key,
                    command_family_code
                )
                VALUES (%s, %s, 'run_reconciliation')
                """,
                (tenant_id, key),
            )
            connection.commit()

        with self.assertRaisesRegex(IdempotencyConflictError, "lifecycle"):
            accept_reconciliation_run(
                command,
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )

    def test_shared_identity_serializes_concurrent_claims(self) -> None:
        """Concurrent transactions leave exactly one durable tenant/key identity."""
        key = f"concurrent-shared-{uuid.uuid4().hex}"
        start = threading.Barrier(2)
        failures: list[BaseException] = []
        successes: list[str] = []
        sqlstates: list[str | None] = []

        with psycopg.connect(posting.DATABASE_URL) as lookup:
            tenant_id = self._tenant_id(lookup)

        def claim(command_family_code: str) -> None:
            try:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    start.wait(timeout=5)
                    connection.execute(
                        """
                        INSERT INTO accounting_core.reconciliation_command_identity (
                            tenant_account_id,
                            reconciliation_command_identity_key,
                            command_family_code
                        )
                        VALUES (%s, %s, %s)
                        """,
                        (tenant_id, key, command_family_code),
                    )
                    connection.commit()
                    successes.append(command_family_code)
            except psycopg.Error as error:
                sqlstates.append(error.sqlstate)
            except BaseException as error:  # captured for the main test thread
                failures.append(error)

        opening = threading.Thread(
            target=claim,
            args=("run_opening",),
            name="reconciliation-key-opening",
        )
        reconciliation = threading.Thread(
            target=claim,
            args=("run_reconciliation",),
            name="reconciliation-key-reconciliation",
        )
        opening.start()
        reconciliation.start()
        opening.join(timeout=10)
        reconciliation.join(timeout=10)
        self.assertFalse(opening.is_alive())
        self.assertFalse(reconciliation.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(successes), 1)
        self.assertEqual(sqlstates, ["23505"])

        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT command_family_code
                FROM accounting_core.reconciliation_command_identity
                WHERE tenant_account_id = %s
                  AND reconciliation_command_identity_key = %s
                """,
                (tenant_id, key),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], successes[0])


if __name__ == "__main__":
    unittest.main()