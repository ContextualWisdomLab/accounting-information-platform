"""PostgreSQL contracts for reconciliation review lock ordering and recovery copy."""

from __future__ import annotations

from pathlib import Path
import queue
import threading
import time
import unittest

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


class PostgresReconciliationLockOrderRedTests(unittest.TestCase):
    """Require approval and allocation paths to acquire match locks consistently."""

    @classmethod
    def setUpClass(cls) -> None:
        allocation.PostgresReconciliationAllocationRedTests.setUpClass()

    def setUp(self) -> None:
        self.case = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _proposed_balanced_match(self) -> object:
        candidate_id = self.case._insert_candidate(
            "stmt-lock-order",
            "journal-lock-order",
            statement_amount="1000.00",
            journal_amount="1000.00",
        )
        match_id = self.case._insert_match(candidate_id)
        self.case._insert_allocations(
            match_id,
            "stmt-lock-order",
            "journal-lock-order",
            "500.00",
        )
        return match_id

    def test_missing_approval_and_concurrent_allocation_do_not_deadlock(self) -> None:
        """An invalid terminal approval fails normally while a concurrent allocation waits."""
        match_id = self._proposed_balanced_match()
        worker_ready = threading.Event()
        worker_outcome: queue.Queue[str] = queue.Queue()
        worker_pid: queue.Queue[int] = queue.Queue()

        with psycopg.connect(posting.DATABASE_URL) as approval_connection:
            approval_pid = approval_connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            approval_connection.execute(
                """
                SELECT 1
                FROM accounting_core.reconciliation_match
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                FOR UPDATE
                """,
                (self.case.scope["tenant_account_id"], self.case.run_reference, match_id),
            )

            def insert_allocation() -> None:
                with psycopg.connect(posting.DATABASE_URL) as allocation_connection:
                    allocation_connection.execute("SET LOCAL statement_timeout = '5s'")
                    worker_pid.put(
                        allocation_connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                    )
                    worker_ready.set()
                    try:
                        allocation_connection.execute(
                            """
                            INSERT INTO accounting_core.statement_match_allocation (
                                tenant_account_id,
                                reconciliation_run_id,
                                reconciliation_match_id,
                                statement_entry_reference,
                                allocated_amount
                            )
                            VALUES (%s, %s, %s, %s, '250.00')
                            """,
                            (
                                self.case.scope["tenant_account_id"],
                                self.case.run_reference,
                                match_id,
                                "stmt-lock-order",
                            ),
                        )
                        allocation_connection.commit()
                    except psycopg.errors.DeadlockDetected:
                        allocation_connection.rollback()
                        worker_outcome.put("deadlock")
                    except Exception as error:  # pragma: no cover - diagnostic boundary
                        allocation_connection.rollback()
                        worker_outcome.put(type(error).__name__)
                    else:
                        worker_outcome.put("inserted")

            worker = threading.Thread(
                target=insert_allocation,
                name="reconciliation-lock-order-allocation",
            )
            worker.start()
            self.assertTrue(worker_ready.wait(timeout=5), "Allocation worker did not start.")
            allocation_pid = worker_pid.get(timeout=1)

            deadline = time.monotonic() + 5
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as monitor:
                while time.monotonic() < deadline:
                    blocked_by_approval = monitor.execute(
                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                        (approval_pid, allocation_pid),
                    ).fetchone()[0]
                    if blocked_by_approval:
                        break
                else:
                    self.fail("Concurrent allocation never reached the parent-match lock boundary.")

            approval_outcome = "unexpected-success"
            try:
                approval_connection.execute("SET LOCAL statement_timeout = '5s'")
                approval_connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET match_status_code = 'approved', approved_at = clock_timestamp()
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (self.case.scope["tenant_account_id"], self.case.run_reference, match_id),
                )
            except psycopg.errors.CheckViolation as error:
                approval_outcome = str(error)
                approval_connection.rollback()
            except psycopg.errors.DeadlockDetected:
                approval_outcome = "deadlock"
                approval_connection.rollback()
            else:
                approval_connection.rollback()

            worker.join(timeout=6)
            self.assertFalse(worker.is_alive(), "Concurrent allocation did not reach a bounded outcome.")

        self.assertNotEqual(approval_outcome, "deadlock", "Approval path deadlocked on lock-order inversion.")
        self.assertIn(
            "reconciliation_approval_required",
            approval_outcome,
            "Missing approval evidence must fail with the stable accounting-control error.",
        )
        self.assertEqual(
            worker_outcome.get_nowait(),
            "inserted",
            "The valid proposed allocation should continue after the rejected terminal transition.",
        )


class ReconciliationRecoveryCopyContractTests(unittest.TestCase):
    """Keep reviewed-allocation recovery instructions executable by operators."""

    def test_frozen_allocation_errors_require_a_new_reconciliation_run(self) -> None:
        """Both database-owned freeze errors name the viable immutable-history recovery."""
        expected_action = "create a new reconciliation run with a new candidate and proposed match"
        migration_contracts = (
            (
                "database/migrations/0015_reconciliation_multi_match_conservation.sql",
                "reconciliation_allocation_frozen",
            ),
            (
                "database/migrations/0016_reconciliation_approval_evidence.sql",
                "reconciliation_snapshot_frozen",
            ),
        )
        for migration_path, stable_error in migration_contracts:
            sql = Path(migration_path).read_text()
            matching_lines = [line for line in sql.splitlines() if stable_error in line]
            self.assertEqual(len(matching_lines), 1, f"Expected one {stable_error} operator message.")
            self.assertIn(expected_action, matching_lines[0])


if __name__ == "__main__":
    unittest.main()
