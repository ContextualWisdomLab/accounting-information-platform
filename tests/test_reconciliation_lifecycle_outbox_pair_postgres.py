"""Real PostgreSQL RED for reconciliation lifecycle command/status/outbox atomicity."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleOutboxPairPostgresTests(unittest.TestCase):
    """Require the lifecycle command, reconciled status, and outbox to commit together."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating run over retained bank-statement evidence."""
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
        """Resolve the internal tenant identity for the opened reconciliation run."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def test_direct_transition_and_status_cannot_commit_without_outbox(self) -> None:
        """Direct SQL cannot create reconciled authority while omitting its event receipt."""
        transition_key = f"missing-outbox-{uuid.uuid4().hex}"
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
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
                VALUES (%s, %s, %s, 'reconciled', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    transition_key,
                    "sha256:" + "d" * 64,
                    "sha256:" + "1" * 64,
                    "sha256:" + "2" * 64,
                    "sha256:" + "0" * 64,
                    "urn:cwl:principal:database_authority_test",
                    "month_end_reconciliation",
                    datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc),
                ),
            )
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_run
                SET run_status_code = 'reconciled'
                WHERE tenant_account_id = %s AND reconciliation_run_id = %s
                """,
                (tenant_id, self.opened["reconciliation_run_id"]),
            )
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_lifecycle_atomic_outbox",
            ):
                connection.commit()
            connection.rollback()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            status = connection.execute(
                """
                SELECT run_status_code
                FROM accounting_core.reconciliation_run
                WHERE tenant_account_id = %s AND reconciliation_run_id = %s
                """,
                (tenant_id, self.opened["reconciliation_run_id"]),
            ).fetchone()[0]
            transition_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.reconciliation_run_transition_command
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_transition_idempotency_key = %s
                """,
                (tenant_id, self.opened["reconciliation_run_id"], transition_key),
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND aggregate_reference = %s
                  AND event_type_code = 'reconciliation_run_reconciled'
                """,
                (
                    tenant_id,
                    "urn:cwl:accounting:reconciliation_run:"
                    + str(self.opened["reconciliation_run_id"]),
                ),
            ).fetchone()[0]

        self.assertIn(status, {"evaluating", "review_required"})
        self.assertEqual(transition_count, 0)
        self.assertEqual(outbox_count, 0)


if __name__ == "__main__":
    unittest.main()
