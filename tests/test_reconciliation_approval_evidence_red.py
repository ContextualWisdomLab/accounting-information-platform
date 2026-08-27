"""RED contracts for explicit, durable reconciliation approval evidence."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0016_reconciliation_approval_evidence.sql"


class ReconciliationApprovalMigrationRedTests(unittest.TestCase):
    """Require human approval to be a normalized immutable accounting-control fact."""

    def test_migration_defines_tenant_scoped_reconciliation_approval(self) -> None:
        """Approval is a 3NF row with command identity, actor, purpose, decision, and provenance."""
        self.assertTrue(
            MIGRATION.exists(),
            "Add migration 0016 for durable reconciliation approval evidence before approved matches can be treated as reviewed control evidence.",
        )
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        self.assertIn(
            "create table accounting_core.reconciliation_approval",
            normalized,
        )
        for column_name in (
            "reconciliation_approval_id",
            "tenant_account_id",
            "reconciliation_run_id",
            "reconciliation_match_id",
            "approval_command_key",
            "source_payload_hash",
            "approver_reference",
            "approval_purpose_code",
            "approval_decision_code",
            "effective_at",
            "recorded_at",
        ):
            self.assertIn(column_name, normalized)

        self.assertIn("force row level security", normalized)
        self.assertIn("reconciliation_approval_immutability_guard", normalized)
        self.assertIn("reconciliation_match_requires_approval_guard", normalized)
        self.assertNotIn("jsonb", normalized)


class PostgresReconciliationApprovalRedTests(unittest.TestCase):
    """Prove approval evidence is enforced by PostgreSQL rather than caller convention."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_reconciliation_approval_table_is_forced_rls_and_append_only(self) -> None:
        """Approval rows are tenant-isolated immutable control evidence."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'accounting_core'
                  AND c.relname = 'reconciliation_approval'
                """
            ).fetchone()
            self.assertEqual(
                row,
                (True, True),
                "Create reconciliation_approval with ENABLE/FORCE RLS; approval evidence may not rely on application-only tenant filtering.",
            )

            trigger_names = {
                item[0]
                for item in connection.execute(
                    """
                    SELECT t.tgname
                    FROM pg_trigger AS t
                    JOIN pg_class AS c ON c.oid = t.tgrelid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'accounting_core'
                      AND c.relname = 'reconciliation_approval'
                      AND NOT t.tgisinternal
                    """
                ).fetchall()
            }
            self.assertIn(
                "reconciliation_approval_immutability_guard",
                trigger_names,
                "Recorded approval evidence must reject UPDATE/DELETE; corrections require a new reviewed match/control fact.",
            )

    def test_approved_match_requires_durable_approval_guard(self) -> None:
        """A caller cannot turn a proposed match into an approved match by status update alone."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            trigger_names = {
                item[0]
                for item in connection.execute(
                    """
                    SELECT t.tgname
                    FROM pg_trigger AS t
                    JOIN pg_class AS c ON c.oid = t.tgrelid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'accounting_core'
                      AND c.relname = 'reconciliation_match'
                      AND NOT t.tgisinternal
                    """
                ).fetchall()
            }
            self.assertIn(
                "reconciliation_match_requires_approval_guard",
                trigger_names,
                "Protect the proposed->approved transition with database-owned reconciliation approval evidence; application status alone is not approval authority.",
            )


if __name__ == "__main__":
    unittest.main()
