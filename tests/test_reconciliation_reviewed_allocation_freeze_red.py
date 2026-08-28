"""RED contracts for freezing reviewed reconciliation allocation evidence."""

from __future__ import annotations

import queue
import threading
import unittest

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


class PostgresReviewedAllocationFreezeRedTests(unittest.TestCase):
    """Require reviewed match allocations to stop changing after approval."""

    @classmethod
    def setUpClass(cls) -> None:
        allocation.PostgresReconciliationAllocationRedTests.setUpClass()

    def setUp(self) -> None:
        self.case = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _proposed_partial_match(self) -> object:
        candidate_id = self.case._insert_candidate(
            "stmt-reviewed-freeze",
            "journal-reviewed-freeze",
            statement_amount="1000.00",
            journal_amount="1000.00",
        )
        match_id = self.case._insert_match(candidate_id)
        self.case._insert_allocations(
            match_id,
            "stmt-reviewed-freeze",
            "journal-reviewed-freeze",
            "500.00",
        )
        return match_id

    def _approved_partial_match(self) -> object:
        match_id = self._proposed_partial_match()
        self.case._approve_match(match_id)
        return match_id

    def test_approved_match_rejects_late_statement_allocation(self) -> None:
        """Approval freezes statement allocations even when unused source capacity remains."""
        match_id = self._approved_partial_match()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_core.statement_match_allocation (
                        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                        statement_entry_reference, allocated_amount
                    )
                    VALUES (%s, %s, %s, %s, '250.00')
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        match_id,
                        "stmt-reviewed-freeze",
                    ),
                )

    def test_approved_match_rejects_late_journal_allocation(self) -> None:
        """Approval freezes journal allocations even when unused source capacity remains."""
        match_id = self._approved_partial_match()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_core.journal_match_allocation (
                        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                        journal_reference, allocated_amount
                    )
                    VALUES (%s, %s, %s, %s, '250.00')
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        match_id,
                        "journal-reviewed-freeze",
                    ),
                )

    def test_approval_serializes_with_concurrent_statement_allocation(self) -> None:
        """An allocation cannot cross the database-owned approval snapshot boundary."""
        match_id = self._proposed_partial_match()
        start_barrier = threading.Barrier(2)
        outcome: queue.Queue[str] = queue.Queue()

        def insert_while_approval_is_uncommitted() -> None:
            with psycopg.connect(posting.DATABASE_URL) as allocation_connection:
                allocation_connection.execute("SET LOCAL lock_timeout = '500ms'")
                start_barrier.wait()
                try:
                    allocation_connection.execute(
                        """
                        INSERT INTO accounting_core.statement_match_allocation (
                            tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                            statement_entry_reference, allocated_amount
                        )
                        VALUES (%s, %s, %s, %s, '250.00')
                        """,
                        (
                            self.case.scope["tenant_account_id"],
                            self.case.run_reference,
                            match_id,
                            "stmt-reviewed-freeze",
                        ),
                    )
                    allocation_connection.commit()
                except psycopg.errors.LockNotAvailable:
                    allocation_connection.rollback()
                    outcome.put("serialized")
                except psycopg.errors.CheckViolation:
                    allocation_connection.rollback()
                    outcome.put("rejected")
                else:
                    outcome.put("inserted")

        with psycopg.connect(posting.DATABASE_URL) as approval_connection:
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
            worker = threading.Thread(
                target=insert_while_approval_is_uncommitted,
                name="concurrent-reconciliation-allocation",
            )
            worker.start()
            start_barrier.wait()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "Concurrent allocation did not reach a bounded database outcome.")
            self.assertIn(
                outcome.get_nowait(),
                {"serialized", "rejected"},
                "A concurrent allocation committed across the uncommitted approval boundary.",
            )
            approval_connection.commit()

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_core.statement_match_allocation (
                        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                        statement_entry_reference, allocated_amount
                    )
                    VALUES (%s, %s, %s, %s, '250.00')
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        match_id,
                        "stmt-reviewed-freeze",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
