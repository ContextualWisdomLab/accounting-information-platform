"""Regression tests for reviewed-evidence authority on superseded matches."""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


ROOT = Path(__file__).resolve().parents[1]
APPROVAL_MIGRATION = ROOT / "database/migrations/0016_reconciliation_approval_evidence.sql"


class ReconciliationSupersedeContractTests(unittest.TestCase):
    """Keep supersession a reviewed-state transition rather than a bypass state."""

    def test_migration_requires_reviewed_predecessor_for_superseded_status(self) -> None:
        """The database trigger must reject direct or unreviewed supersession."""
        sql = APPROVAL_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("IF NEW.match_status_code = 'superseded' THEN", sql)
        self.assertIn(
            "OLD.match_status_code NOT IN ('approved', 'rejected', 'superseded')",
            sql,
        )
        self.assertIn("reconciliation_supersede_requires_reviewed_decision", sql)


class ReconciliationSupersedeAuthorityPostgresTests(unittest.TestCase):
    """Prove raw SQL cannot manufacture evidence-free superseded matches."""

    @classmethod
    def setUpClass(cls) -> None:
        """Provision the real PostgreSQL foundation used by accounting acceptance tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating reconciliation run for each authority regression."""
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

    def _insert_candidate(self, connection: psycopg.Connection) -> tuple[object, object]:
        """Insert one reviewable candidate and return its tenant and candidate ids."""
        run_id = self.opened["reconciliation_run_id"]
        tenant_id = connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (run_id,),
        ).fetchone()[0]
        candidate_id = connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_candidate (
                tenant_account_id,
                reconciliation_run_id,
                statement_entry_reference,
                journal_reference,
                statement_amount,
                journal_amount,
                rule_code
            )
            VALUES (%s, %s, %s, %s, 100.000000, 100.000000, 'exact_reference')
            RETURNING reconciliation_candidate_id
            """,
            (
                tenant_id,
                run_id,
                f"urn:cwl:bank:statement-entry:{uuid4()}",
                f"urn:cwl:accounting:general_journal:{uuid4()}",
            ),
        ).fetchone()[0]
        return tenant_id, candidate_id

    def test_direct_superseded_insert_requires_prior_review_evidence(self) -> None:
        """A caller cannot insert a terminal superseded match without a decision."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, candidate_id = self._insert_candidate(connection)
            with self.assertRaisesRegex(
                psycopg.Error, "superseded only from approved or rejected"
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_match (
                        tenant_account_id,
                        reconciliation_run_id,
                        reconciliation_candidate_id,
                        match_status_code
                    )
                    VALUES (%s, %s, %s, 'superseded')
                    """,
                    (tenant_id, self.opened["reconciliation_run_id"], candidate_id),
                )
            connection.rollback()

    def test_proposed_match_cannot_skip_review_by_becoming_superseded(self) -> None:
        """A proposed match must be approved or rejected before supersession."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, candidate_id = self._insert_candidate(connection)
            match_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    tenant_account_id,
                    reconciliation_run_id,
                    reconciliation_candidate_id,
                    match_status_code
                )
                VALUES (%s, %s, %s, 'proposed')
                RETURNING reconciliation_match_id
                """,
                (tenant_id, self.opened["reconciliation_run_id"], candidate_id),
            ).fetchone()[0]
            with self.assertRaisesRegex(
                psycopg.Error, "superseded only from approved or rejected"
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET match_status_code = 'superseded'
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        tenant_id,
                        self.opened["reconciliation_run_id"],
                        match_id,
                    ),
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
