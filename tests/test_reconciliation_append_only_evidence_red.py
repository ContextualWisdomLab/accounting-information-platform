"""RED contracts for append-only reconciliation candidate/allocation evidence."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


class PostgresReconciliationAppendOnlyEvidenceRedTests(unittest.TestCase):
    """Require recorded reconciliation source and allocation evidence to remain immutable."""

    @classmethod
    def setUpClass(cls) -> None:
        allocation.PostgresReconciliationAllocationRedTests.setUpClass()

    def setUp(self) -> None:
        self.case = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_recorded_candidate_source_identity_cannot_be_updated(self) -> None:
        """Candidate identity/capacity is immutable once recorded."""
        candidate_id = self.case._insert_candidate("stmt-immutable", "journal-immutable")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_candidate
                    SET statement_entry_reference = 'stmt-relabelled',
                        statement_amount = 2000.00
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_candidate_id = %s
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        candidate_id,
                    ),
                )

    def test_recorded_unmatched_candidate_cannot_be_deleted(self) -> None:
        """Rejected/unselected candidates remain durable evidence rather than disappearing."""
        candidate_id = self.case._insert_candidate("stmt-retained", "journal-retained")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    DELETE FROM accounting_core.reconciliation_candidate
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_candidate_id = %s
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        candidate_id,
                    ),
                )

    def _superseded_match(self, statement_reference: str, journal_reference: str) -> uuid.UUID:
        candidate_id = self.case._insert_candidate(statement_reference, journal_reference)
        match_id = self.case._insert_match(candidate_id)
        self.case._insert_allocations(
            match_id,
            statement_reference,
            journal_reference,
            "1000.00",
        )
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = 'superseded'
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (
                    self.case.scope["tenant_account_id"],
                    self.case.run_reference,
                    match_id,
                ),
            )
        return match_id

    def test_superseded_statement_allocation_cannot_be_updated_or_deleted(self) -> None:
        """Superseding releases capacity through status, never by rewriting allocation history."""
        match_id = self._superseded_match("stmt-history", "journal-history")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    UPDATE accounting_core.statement_match_allocation
                    SET allocated_amount = 500.00
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        match_id,
                    ),
                )
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    DELETE FROM accounting_core.statement_match_allocation
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        match_id,
                    ),
                )

    def test_superseded_journal_allocation_cannot_be_updated_or_deleted(self) -> None:
        """Journal-side allocation history remains immutable after explicit release."""
        match_id = self._superseded_match("stmt-journal-history", "journal-journal-history")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    UPDATE accounting_core.journal_match_allocation
                    SET allocated_amount = 500.00
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        match_id,
                    ),
                )
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    DELETE FROM accounting_core.journal_match_allocation
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        self.case.scope["tenant_account_id"],
                        self.case.run_reference,
                        match_id,
                    ),
                )


if __name__ == "__main__":
    unittest.main()
