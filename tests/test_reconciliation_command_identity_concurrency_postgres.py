"""Real PostgreSQL regression for cross-family reconciliation command identity."""

from __future__ import annotations

import queue
import threading
import unittest
import uuid
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting


_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "database"
    / "migrations"
    / "0019_reconciliation_run_command_evidence.sql"
)


class ReconciliationCommandIdentityConcurrencyPostgresTests(unittest.TestCase):
    """Keep one tenant idempotency key owned by exactly one command family."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the canonical migration chain before concurrency assertions."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated tenant whose command identity can be contended."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_migration_routes_both_command_families_through_shared_identity(self) -> None:
        """Opening and reconciliation command triggers reserve the same keyspace."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE TABLE accounting_core.reconciliation_command_identity",
            migration,
        )
        self.assertIn(
            "PRIMARY KEY (tenant_account_id, reconciliation_command_identity_key)",
            migration,
        )
        self.assertIn(
            "CREATE TRIGGER accounting_reconciliation_run_command_identity_guard",
            migration,
        )
        self.assertIn(
            "NEW.reconciliation_idempotency_key,\n        'run_opening'",
            migration,
        )
        self.assertIn(
            "CREATE TRIGGER accounting_reconciliation_transition_command_identity_guard",
            migration,
        )
        self.assertIn(
            "NEW.reconciliation_transition_idempotency_key,\n        'run_reconciliation'",
            migration,
        )
        self.assertIn("reconciliation_command_identity_conflict", migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)

    def test_two_command_families_cannot_concurrently_reserve_same_key(self) -> None:
        """The database unique identity, not thread scheduling, selects one owner."""
        identity_key = f"cross-family-{uuid.uuid4().hex}"
        barrier = threading.Barrier(2)
        outcomes: queue.Queue[tuple[str, str]] = queue.Queue()

        def reserve(identity_family_code: str) -> None:
            try:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    connection.execute("SET lock_timeout = '5s'")
                    barrier.wait(timeout=5)
                    try:
                        connection.execute(
                            """
                            SELECT accounting_core.reserve_reconciliation_command_identity(
                                %s, %s, %s
                            )
                            """,
                            (
                                self.case.tenant_id,
                                identity_key,
                                identity_family_code,
                            ),
                        )
                        connection.commit()
                    except psycopg.errors.UniqueViolation:
                        connection.rollback()
                        outcomes.put((identity_family_code, "conflict"))
                    else:
                        outcomes.put((identity_family_code, "reserved"))
            except Exception as error:  # pragma: no cover - diagnostic branch
                outcomes.put((identity_family_code, f"error:{type(error).__name__}:{error}"))

        workers = [
            threading.Thread(target=reserve, args=("run_opening",)),
            threading.Thread(target=reserve, args=("run_reconciliation",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive(), "command-identity contender did not finish")

        observed = sorted(outcomes.get_nowait() for _ in range(outcomes.qsize()))
        self.assertEqual(len(observed), 2, observed)
        self.assertEqual(
            sorted(outcome for _family, outcome in observed),
            ["conflict", "reserved"],
            observed,
        )

        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT command_family_code
                FROM accounting_core.reconciliation_command_identity
                WHERE tenant_account_id = %s
                  AND reconciliation_command_identity_key = %s
                """,
                (self.case.tenant_id, identity_key),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0][0], {"run_opening", "run_reconciliation"})


if __name__ == "__main__":
    unittest.main()
