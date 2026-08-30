"""RED contracts for cross-run reconciliation source conservation."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


class PostgresCrossRunReconciliationConservationRedTests(unittest.TestCase):
    """Require one immutable source amount to be consumed only once across active runs."""

    @classmethod
    def setUpClass(cls) -> None:
        allocation.PostgresReconciliationAllocationRedTests.setUpClass()

    def setUp(self) -> None:
        self.case = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _insert_run(self) -> uuid.UUID:
        run_reference = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.case.scope["tenant_account_id"]),),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_run (
                    reconciliation_run_id, tenant_account_id, legal_entity_id,
                    accounting_book_id, bank_account_assignment_id, currency_code,
                    bank_cutoff_at, book_cutoff_at, matching_policy_version,
                    knowledge_cutoff_at, run_status_code
                )
                VALUES (%s, %s, %s, %s, %s, 'KRW', %s, %s, 'policy-v1', %s, 'evaluating')
                """,
                (
                    run_reference,
                    self.case.scope["tenant_account_id"],
                    self.case.scope["legal_entity_id"],
                    self.case.scope["accounting_book_id"],
                    self.case.scope["bank_account_assignment_id"],
                    allocation.VALID_FROM,
                    allocation.VALID_FROM,
                    allocation.VALID_FROM,
                ),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_run_command (
                    tenant_account_id, reconciliation_run_id, bank_statement_record_id,
                    reconciliation_idempotency_key, reconciliation_command_hash,
                    source_payload_hash, source_payload_reference
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self.case.scope["tenant_account_id"],
                    run_reference,
                    self.case.statement_record["bank_statement_record_id"],
                    f"run-evidence-{uuid.uuid4().hex}",
                    "sha256:" + "c" * 64,
                    self.case.statement_record["source_artifact_hash"],
                    f"memory:{self.case.statement_record['source_artifact_hash']}",
                ),
            )
            connection.commit()
        return run_reference

    def _insert_candidate(
        self,
        run_reference: uuid.UUID,
        statement_reference: str,
        journal_reference: str,
        *,
        statement_amount: str = "1000.00",
        journal_amount: str = "1000.00",
    ) -> uuid.UUID:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    reconciliation_candidate_id, tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference, statement_amount,
                    journal_amount, rule_code
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'provider_reference')
                RETURNING reconciliation_candidate_id
                """,
                (
                    uuid.uuid4(),
                    self.case.scope["tenant_account_id"],
                    run_reference,
                    statement_reference,
                    journal_reference,
                    statement_amount,
                    journal_amount,
                ),
            ).fetchone()
        return row[0]

    def _insert_match(self, run_reference: uuid.UUID, candidate_id: uuid.UUID) -> uuid.UUID:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,
                    reconciliation_candidate_id, match_status_code, approved_at
                )
                VALUES (%s, %s, %s, %s, 'proposed', NULL)
                RETURNING reconciliation_match_id
                """,
                (
                    uuid.uuid4(),
                    self.case.scope["tenant_account_id"],
                    run_reference,
                    candidate_id,
                ),
            ).fetchone()
        return row[0]

    def _approve_match(self, run_reference: uuid.UUID, match_id: uuid.UUID) -> None:
        self.case._record_approval(match_id, run_reference=run_reference)
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = 'approved', approved_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (self.case.scope["tenant_account_id"], run_reference, match_id),
            )

    def _insert_statement_allocation(
        self,
        run_reference: uuid.UUID,
        match_id: uuid.UUID,
        statement_reference: str,
        amount: str = "1000.00",
    ) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.statement_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    statement_entry_reference, allocated_amount
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    self.case.scope["tenant_account_id"],
                    run_reference,
                    match_id,
                    statement_reference,
                    amount,
                ),
            )

    def _insert_journal_allocation(
        self,
        run_reference: uuid.UUID,
        match_id: uuid.UUID,
        journal_reference: str,
        amount: str = "1000.00",
    ) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.journal_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    journal_reference, allocated_amount
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    self.case.scope["tenant_account_id"],
                    run_reference,
                    match_id,
                    journal_reference,
                    amount,
                ),
            )

    def _insert_balanced_allocations(
        self,
        run_reference: uuid.UUID,
        match_id: uuid.UUID,
        statement_reference: str,
        journal_reference: str,
        amount: str = "1000.00",
    ) -> None:
        self._insert_statement_allocation(run_reference, match_id, statement_reference, amount)
        self._insert_journal_allocation(run_reference, match_id, journal_reference, amount)

    def test_second_active_run_cannot_reconsume_statement_source(self) -> None:
        """An approved statement amount is conserved across active reconciliation runs."""
        first_candidate = self.case._insert_candidate("stmt-cross-run", "journal-a")
        first_match = self.case._insert_match(first_candidate)
        self.case._insert_allocations(first_match, "stmt-cross-run", "journal-a", "1000.00")
        self.case._approve_match(first_match)

        second_run = self._insert_run()
        second_candidate = self._insert_candidate(second_run, "stmt-cross-run", "journal-b")
        second_match = self._insert_match(second_run, second_candidate)
        self._insert_balanced_allocations(
            second_run, second_match, "stmt-cross-run", "journal-b"
        )

        with self.assertRaises(psycopg.errors.CheckViolation):
            self._approve_match(second_run, second_match)

    def test_second_active_run_cannot_reconsume_journal_source(self) -> None:
        """An approved journal amount is conserved across active reconciliation runs."""
        first_candidate = self.case._insert_candidate("stmt-a", "journal-cross-run")
        first_match = self.case._insert_match(first_candidate)
        self.case._insert_allocations(first_match, "stmt-a", "journal-cross-run", "1000.00")
        self.case._approve_match(first_match)

        second_run = self._insert_run()
        second_candidate = self._insert_candidate(second_run, "stmt-b", "journal-cross-run")
        second_match = self._insert_match(second_run, second_candidate)
        self._insert_balanced_allocations(
            second_run, second_match, "stmt-b", "journal-cross-run"
        )

        with self.assertRaises(psycopg.errors.CheckViolation):
            self._approve_match(second_run, second_match)

    def test_concurrent_active_runs_serialize_same_statement_source(self) -> None:
        """Two reviewers cannot concurrently approve consumption of the same statement source."""
        first_candidate = self.case._insert_candidate("stmt-concurrent", "journal-first")
        first_match = self.case._insert_match(first_candidate)
        self.case._insert_allocations(
            first_match, "stmt-concurrent", "journal-first", "1000.00"
        )

        second_run = self._insert_run()
        second_candidate = self._insert_candidate(
            second_run,
            "stmt-concurrent",
            "journal-second",
        )
        second_match = self._insert_match(second_run, second_candidate)
        self._insert_balanced_allocations(
            second_run, second_match, "stmt-concurrent", "journal-second"
        )
        self.case._record_approval(first_match)
        self.case._record_approval(second_match, run_reference=second_run)
        approval_sql = """
            UPDATE accounting_core.reconciliation_match
            SET match_status_code = 'approved', approved_at = clock_timestamp()
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
              AND reconciliation_match_id = %s
        """
        first_connection = psycopg.connect(posting.DATABASE_URL)
        second_connection = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(first_connection.close)
        self.addCleanup(second_connection.close)

        first_connection.execute("SET lock_timeout = '5s'")
        first_connection.execute(
            approval_sql,
            (
                self.case.scope["tenant_account_id"],
                self.case.run_reference,
                first_match,
            ),
        )
        second_connection.execute("SET lock_timeout = '250ms'")
        with self.assertRaises(psycopg.errors.LockNotAvailable):
            second_connection.execute(
                approval_sql,
                (
                    self.case.scope["tenant_account_id"],
                    second_run,
                    second_match,
                ),
            )
        second_connection.rollback()
        first_connection.commit()

        with self.assertRaises(psycopg.errors.CheckViolation):
            second_connection.execute(
                approval_sql,
                (
                    self.case.scope["tenant_account_id"],
                    second_run,
                    second_match,
                ),
            )
        second_connection.rollback()

    def test_superseded_match_explicitly_releases_source_consumption(self) -> None:
        """Superseding the old evidence releases capacity without deleting its history."""
        first_candidate = self.case._insert_candidate("stmt-released", "journal-released")
        first_match = self.case._insert_match(first_candidate)
        self.case._insert_allocations(
            first_match,
            "stmt-released",
            "journal-released",
            "1000.00",
        )
        self.case._approve_match(first_match)
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
                    first_match,
                ),
            )

        second_run = self._insert_run()
        second_candidate = self._insert_candidate(
            second_run,
            "stmt-released",
            "journal-released",
        )
        second_match = self._insert_match(second_run, second_candidate)
        self._insert_balanced_allocations(
            second_run, second_match, "stmt-released", "journal-released"
        )
        self._approve_match(second_run, second_match)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            statuses = connection.execute(
                """
                SELECT match_status_code
                FROM accounting_core.reconciliation_match
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = ANY(%s)
                ORDER BY recorded_at, reconciliation_match_id
                """,
                (
                    self.case.scope["tenant_account_id"],
                    [self.case.run_reference, second_run],
                ),
            ).fetchall()
        self.assertEqual(sorted(row[0] for row in statuses), ["approved", "superseded"])


if __name__ == "__main__":
    unittest.main()
