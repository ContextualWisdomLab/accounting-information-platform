"""PostgreSQL contracts for reconciliation review lock ordering and recovery copy."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import queue
import threading
import time
import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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

    def _wait_until_blocked_by(
        self,
        *,
        blocked_pid: int,
        blocker_pid: int,
        message: str,
    ) -> None:
        """Wait for an exact PostgreSQL blocking edge instead of relying on sleeps."""
        deadline = time.monotonic() + 5
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as monitor:
            while time.monotonic() < deadline:
                if monitor.execute(
                    "SELECT %s = ANY(pg_blocking_pids(%s))",
                    (blocker_pid, blocked_pid),
                ).fetchone()[0]:
                    return
        self.fail(message)

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
                    # Must outlast the monitor barrier plus the approval statement budget.
                    allocation_connection.execute("SET LOCAL statement_timeout = '20s'")
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
                    except psycopg.Error as error:  # pragma: no cover - diagnostic boundary
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
            self._wait_until_blocked_by(
                blocked_pid=allocation_pid,
                blocker_pid=approval_pid,
                message="Concurrent allocation never reached the parent-match lock boundary.",
            )

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

            worker.join(timeout=21)
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

    def test_approval_insert_and_allocation_insert_share_row_then_advisory_order(self) -> None:
        """A queued approval cannot deadlock with a concurrent allocation FK row lock."""
        match_id = self._proposed_balanced_match()
        approval_outcome: queue.Queue[str] = queue.Queue()
        allocation_outcome: queue.Queue[str] = queue.Queue()
        approval_pid_queue: queue.Queue[int] = queue.Queue()
        allocation_pid_queue: queue.Queue[int] = queue.Queue()

        with psycopg.connect(posting.DATABASE_URL) as advisory_holder:
            holder_pid = advisory_holder.execute("SELECT pg_backend_pid()").fetchone()[0]
            advisory_holder.execute(
                "SELECT accounting_core.reconciliation_match_snapshot_lock(%s, %s, %s)",
                (self.case.scope["tenant_account_id"], self.case.run_reference, match_id),
            )

            def insert_approval() -> None:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    connection.execute("SET LOCAL statement_timeout = '20s'")
                    approval_pid_queue.put(
                        connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                    )
                    try:
                        connection.execute(
                            """
                            INSERT INTO accounting_core.reconciliation_approval (
                                tenant_account_id,
                                reconciliation_run_id,
                                reconciliation_match_id,
                                approval_command_key,
                                source_payload_hash,
                                source_payload_reference,
                                reconciliation_snapshot_hash,
                                approver_reference,
                                approval_purpose_code,
                                approval_decision_code,
                                effective_at
                            )
                            VALUES (
                                %s, %s, %s, %s,
                                %s, 'urn:cwl:object:lock-order-approval', %s,
                                'operator-lock-order', 'bank-close-review', 'approved', %s
                            )
                            """,
                            (
                                self.case.scope["tenant_account_id"],
                                self.case.run_reference,
                                match_id,
                                f"approval-lock-order-{uuid.uuid4().hex}",
                                "sha256:" + "1" * 64,
                                "sha256:" + "2" * 64,
                                datetime.now(timezone.utc),
                            ),
                        )
                        connection.commit()
                    except psycopg.errors.DeadlockDetected:
                        connection.rollback()
                        approval_outcome.put("deadlock")
                    except psycopg.Error as error:  # pragma: no cover - diagnostic boundary
                        connection.rollback()
                        approval_outcome.put(f"{type(error).__name__}:{error}")
                    else:
                        approval_outcome.put("approved")

            approval_worker = threading.Thread(
                target=insert_approval,
                name="reconciliation-lock-order-approval-insert",
            )
            approval_worker.start()
            approval_pid = approval_pid_queue.get(timeout=5)
            self._wait_until_blocked_by(
                blocked_pid=approval_pid,
                blocker_pid=holder_pid,
                message="Approval insert never reached the snapshot advisory-lock boundary.",
            )

            def insert_allocation() -> None:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    connection.execute("SET LOCAL statement_timeout = '20s'")
                    allocation_pid_queue.put(
                        connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                    )
                    try:
                        connection.execute(
                            """
                            INSERT INTO accounting_core.statement_match_allocation (
                                tenant_account_id,
                                reconciliation_run_id,
                                reconciliation_match_id,
                                statement_entry_reference,
                                allocated_amount
                            )
                            VALUES (%s, %s, %s, 'stmt-lock-order', '250.00')
                            """,
                            (
                                self.case.scope["tenant_account_id"],
                                self.case.run_reference,
                                match_id,
                            ),
                        )
                        connection.commit()
                    except psycopg.errors.DeadlockDetected:
                        connection.rollback()
                        allocation_outcome.put("deadlock")
                    except psycopg.Error as error:  # pragma: no cover - diagnostic boundary
                        connection.rollback()
                        allocation_outcome.put(f"{type(error).__name__}:{error}")
                    else:
                        allocation_outcome.put("inserted")

            allocation_worker = threading.Thread(
                target=insert_allocation,
                name="reconciliation-lock-order-allocation-insert",
            )
            allocation_worker.start()
            allocation_pid = allocation_pid_queue.get(timeout=5)

            # On the repaired path the allocation waits on the approval's parent-row
            # lock. On the predecessor path it acquires that row and then joins the
            # advisory-lock wait queue. Either way it must be blocked before release.
            deadline = time.monotonic() + 5
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as monitor:
                while time.monotonic() < deadline:
                    blockers = monitor.execute(
                        "SELECT pg_blocking_pids(%s)", (allocation_pid,)
                    ).fetchone()[0]
                    if blockers:
                        break
                else:
                    self.fail("Concurrent allocation never reached a controlled lock boundary.")

            advisory_holder.rollback()
            approval_worker.join(timeout=21)
            allocation_worker.join(timeout=21)
            self.assertFalse(approval_worker.is_alive(), "Approval insert did not finish.")
            self.assertFalse(allocation_worker.is_alive(), "Allocation insert did not finish.")

        approval_result = approval_outcome.get_nowait()
        allocation_result = allocation_outcome.get_nowait()
        self.assertNotEqual(approval_result, "deadlock")
        self.assertNotEqual(allocation_result, "deadlock")
        self.assertEqual(approval_result, "approved")
        self.assertIn("reconciliation_snapshot_frozen", allocation_result)


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
            sql = (REPOSITORY_ROOT / migration_path).read_text(encoding="utf-8")
            matching_lines = [line for line in sql.splitlines() if stable_error in line]
            self.assertEqual(len(matching_lines), 1, f"Expected one {stable_error} operator message.")
            self.assertIn(expected_action, matching_lines[0])


if __name__ == "__main__":
    unittest.main()
