"""RED contracts for freezing reviewed reconciliation allocation evidence."""

from __future__ import annotations

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

    def _approved_partial_match(self) -> object:
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


if __name__ == "__main__":
    unittest.main()
