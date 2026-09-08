"""Real PostgreSQL acceptance for reconciliation lifecycle outbox authority."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
import uuid

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.reconciliation_opening_book_fixture import post_reconciliation_opening_book_balance
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleOutboxPostgresTests(unittest.TestCase):
    """Require lifecycle command, reconciled state, and publication evidence to commit together."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        post_reconciliation_opening_book_balance(self.fixture.case)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )

    def _tenant_id(self, connection: psycopg.Connection) -> object:
        """Resolve the internal tenant identity for the opened run."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def _begin_safe_raw_transition(self, connection: psycopg.Connection) -> None:
        """Enter the database-proven lease/fresh-snapshot lifecycle protocol."""
        lifecycle_scope = (
            "reconciliation_run_lifecycle:" + self.opened["reconciliation_run_id"]
        )
        connection.execute(
            "SELECT accounting_core.acquire_reconciliation_lifecycle_session(%s, %s)",
            (
                self.fixture.case.policy.tenant_reference,
                self.opened["reconciliation_run_id"],
            ),
        )
        connection.commit()
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (self.fixture.case.policy.tenant_reference, lifecycle_scope),
        )

    def _release_safe_raw_transition(self, connection: psycopg.Connection) -> None:
        """Release the test session lease so fixture teardown has no stale FK row."""
        released = connection.execute(
            "SELECT accounting_core.release_reconciliation_lifecycle_session(%s, %s)",
            (
                self.fixture.case.policy.tenant_reference,
                self.opened["reconciliation_run_id"],
            ),
        ).fetchone()
        self.assertIsNotNone(released)
        self.assertTrue(bool(released[0]))
        connection.commit()

    def _insert_transition(self, connection: psycopg.Connection) -> tuple[object, str]:
        """Insert a lawful transition command and return its database-owned identity and hash."""
        return connection.execute(
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
            VALUES (%s, %s, %s, 'reconciled', %s, %s, %s, %s,
                    'urn:cwl:principal:test_controller',
                    'month_end_reconciliation', %s)
            RETURNING reconciliation_run_transition_command_id,
                      reconciliation_transition_command_hash
            """,
            (
                self._tenant_id(connection),
                self.opened["reconciliation_run_id"],
                f"direct-transition-{uuid.uuid4().hex}",
                "sha256:" + "d" * 64,
                "sha256:" + "1" * 64,
                "sha256:" + "2" * 64,
                "sha256:" + "0" * 64,
                datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            ),
        ).fetchone()

    def _complete_with_outbox(
        self, connection: psycopg.Connection
    ) -> tuple[object, object, str]:
        """Commit the exact raw three-way lifecycle fact and return its event identity."""
        run_id = self.opened["reconciliation_run_id"]
        self._begin_safe_raw_transition(connection)
        tenant_id = self._tenant_id(connection)
        transition_id, transition_hash = self._insert_transition(connection)
        connection.execute(
            """
            UPDATE accounting_core.reconciliation_run
            SET run_status_code = 'reconciled'
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
            """,
            (tenant_id, run_id),
        )
        outbox_event_id = connection.execute(
            """
            INSERT INTO accounting_integration.outbox_event (
                tenant_account_id,
                event_type_code,
                aggregate_reference,
                payload_reference,
                payload_hash
            )
            VALUES (%s, 'reconciliation_run_reconciled', %s, %s, %s)
            RETURNING outbox_event_id
            """,
            (
                tenant_id,
                f"urn:cwl:accounting:reconciliation_run:{run_id}",
                f"urn:cwl:accounting:reconciliation_run_transition:{transition_id}",
                transition_hash,
            ),
        ).fetchone()[0]
        connection.commit()
        self._release_safe_raw_transition(connection)
        return tenant_id, outbox_event_id, str(transition_hash)

    def test_reconciled_transition_cannot_commit_without_lifecycle_outbox(self) -> None:
        """Authority-bearing reconciled state cannot commit without its exact publication evidence."""
        run_id = self.opened["reconciliation_run_id"]
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._begin_safe_raw_transition(connection)
            self._insert_transition(connection)
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_run
                SET run_status_code = 'reconciled'
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (self._tenant_id(connection), run_id),
            )
            with self.assertRaisesRegex(psycopg.Error, "outbox"):
                connection.commit()
            connection.rollback()
            self._release_safe_raw_transition(connection)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            run_status = connection.execute(
                """
                SELECT run_status_code
                FROM accounting_core.reconciliation_run
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (tenant_id, run_id),
            ).fetchone()[0]
            transition_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.reconciliation_run_transition_command
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (tenant_id, run_id),
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND event_type_code = 'reconciliation_run_reconciled'
                  AND aggregate_reference = %s
                """,
                (tenant_id, f"urn:cwl:accounting:reconciliation_run:{run_id}"),
            ).fetchone()[0]

        self.assertNotEqual(run_status, "reconciled")
        self.assertEqual(transition_count, 0)
        self.assertEqual(outbox_count, 0)

    def test_exact_lifecycle_outbox_commits_and_allows_publication_only(self) -> None:
        """The exact event commits, can be published, and keeps identity/hash immutable."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, outbox_event_id, transition_hash = self._complete_with_outbox(connection)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            row = connection.execute(
                """
                UPDATE accounting_integration.outbox_event
                SET published_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND outbox_event_id = %s
                RETURNING payload_hash, published_at
                """,
                (tenant_id, outbox_event_id),
            ).fetchone()
            connection.commit()
        self.assertEqual(row[0], transition_hash)
        self.assertIsNotNone(row[1])

        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(psycopg.Error, "immutable"):
                connection.execute(
                    """
                    UPDATE accounting_integration.outbox_event
                    SET payload_hash = %s
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    ("sha256:" + "f" * 64, tenant_id, outbox_event_id),
                )
            connection.rollback()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(psycopg.Error, "immutable"):
                connection.execute(
                    """
                    DELETE FROM accounting_integration.outbox_event
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    (tenant_id, outbox_event_id),
                )
            connection.rollback()

    def test_forged_lifecycle_outbox_cannot_commit(self) -> None:
        """A lifecycle event with a wrong transition hash cannot satisfy publication authority."""
        run_id = self.opened["reconciliation_run_id"]
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._begin_safe_raw_transition(connection)
            tenant_id = self._tenant_id(connection)
            transition_id, _transition_hash = self._insert_transition(connection)
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_run
                SET run_status_code = 'reconciled'
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (tenant_id, run_id),
            )
            connection.execute(
                """
                INSERT INTO accounting_integration.outbox_event (
                    tenant_account_id,
                    event_type_code,
                    aggregate_reference,
                    payload_reference,
                    payload_hash
                )
                VALUES (%s, 'reconciliation_run_reconciled', %s, %s, %s)
                """,
                (
                    tenant_id,
                    f"urn:cwl:accounting:reconciliation_run:{run_id}",
                    f"urn:cwl:accounting:reconciliation_run_transition:{transition_id}",
                    "sha256:" + "f" * 64,
                ),
            )
            with self.assertRaisesRegex(psycopg.Error, "outbox"):
                connection.commit()
            connection.rollback()
            self._release_safe_raw_transition(connection)


if __name__ == "__main__":
    unittest.main()
