"""Real PostgreSQL regression for stacked lifecycle session-lock admission."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleStackedSessionAdmissionPostgresTests(unittest.TestCase):
    """Reject stacked raw session holds that masquerade as the required xact lock."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating run for the lifecycle key under test."""
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
                "stacked-session-admission-" + self.opened["reconciliation_run_id"],
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                "sha256:" + "c" * 64,
                "sha256:" + "d" * 64,
                "sha256:" + "0" * 64,
                "urn:cwl:principal:stacked_session_test",
                "month_end_reconciliation",
                "2026-09-08T10:00:00Z",
            ),
        )

    def test_stacked_session_holds_cannot_satisfy_transaction_lock_proof(self) -> None:
        """After the probe, a remaining session hold must not look like an xact lock."""
        tenant_reference = self.fixture.case.policy.tenant_reference
        run_id = self.opened["reconciliation_run_id"]
        lifecycle_scope = f"reconciliation_run_lifecycle:{run_id}"

        with psycopg.connect(posting.DATABASE_URL) as owner:
            try:
                tenant_id = self._tenant_id(owner)
                owner.execute(
                    "SELECT accounting_core.acquire_reconciliation_lifecycle_session(%s, %s)",
                    (tenant_reference, run_id),
                )
                owner.commit()

                # Retain the committed lease while removing the canonical hold.
                released = owner.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                    (tenant_reference, lifecycle_scope),
                ).fetchone()[0]
                owner.commit()
                self.assertTrue(released)

                owner.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                owner.execute("SELECT 1").fetchone()

                # Two raw session holds reproduce the ambiguity: a guard that
                # removes only one hold will still see the other in pg_locks.
                owner.execute(
                    "SELECT pg_advisory_lock(hashtext(%s), hashtext(%s))",
                    (tenant_reference, lifecycle_scope),
                )
                owner.execute(
                    "SELECT pg_advisory_lock(hashtext(%s), hashtext(%s))",
                    (tenant_reference, lifecycle_scope),
                )

                with self.assertRaises(psycopg.Error) as raised:
                    self._raw_transition(owner, tenant_id)
                self.assertIn(
                    "reconciliation_lifecycle_session_lock_required",
                    str(raised.exception),
                )
                owner.rollback()
            finally:
                owner.rollback()
                owner.execute("SELECT pg_advisory_unlock_all()")
                owner.commit()


if __name__ == "__main__":
    unittest.main()
