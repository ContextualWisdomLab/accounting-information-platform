"""RED contracts for durable reconciliation-run and exception evidence."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0013_reconciliation_run_exception_evidence.sql"


class ReconciliationRunExceptionMigrationRedTests(unittest.TestCase):
    """Require a normalized durable control plane before reconciliation can close."""

    def test_migration_defines_normalized_run_exception_and_evidence_rows(self) -> None:
        """Run scope and operator exceptions are durable 3NF evidence, not transient JSON."""
        self.assertTrue(
            MIGRATION.exists(),
            "Add migration 0013 for durable reconciliation run/exception evidence before treating proposals as close evidence.",
        )
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for object_name in (
            "reconciliation_run",
            "reconciliation_exception",
            "reconciliation_evidence",
        ):
            self.assertIn(f"create table accounting_core.{object_name}", normalized)

        for column_name in (
            "tenant_account_id",
            "legal_entity_id",
            "accounting_book_id",
            "bank_account_assignment_id",
            "currency_code",
            "bank_cutoff_at",
            "book_cutoff_at",
            "matching_policy_version",
            "knowledge_cutoff_at",
            "run_status_code",
            "recorded_at",
        ):
            self.assertIn(column_name, normalized)

        for column_name in (
            "exception_code",
            "owner_reference",
            "next_action",
            "effective_at",
            "recorded_at",
            "resolution_status_code",
        ):
            self.assertIn(column_name, normalized)

        self.assertNotIn("jsonb", normalized)
        self.assertIn("force row level security", normalized)
        self.assertIn("reconciliation_run_scope_guard", normalized)
        self.assertIn("reject_reconciliation_run_scope_mutation", normalized)


@unittest.skipUnless(MIGRATION.exists(), "RED until durable reconciliation migration exists")
class PostgresReconciliationRunExceptionRedTests(unittest.TestCase):
    """Prove tenant isolation and immutable evaluated-run scope in real PostgreSQL."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_reconciliation_control_tables_force_rls_and_scope_guard(self) -> None:
        """Authoritative reconciliation evidence is tenant-forced and run scope is guarded."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'accounting_core'
                  AND c.relname = ANY(%s)
                ORDER BY c.relname
                """,
                ([
                    "reconciliation_evidence",
                    "reconciliation_exception",
                    "reconciliation_run",
                ],),
            ).fetchall()
            self.assertEqual(
                rows,
                [
                    ("reconciliation_evidence", True, True),
                    ("reconciliation_exception", True, True),
                    ("reconciliation_run", True, True),
                ],
            )

            trigger = connection.execute(
                """
                SELECT tgname
                FROM pg_trigger AS t
                JOIN pg_class AS c ON c.oid = t.tgrelid
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'accounting_core'
                  AND c.relname = 'reconciliation_run'
                  AND tgname = 'reconciliation_run_scope_guard'
                  AND NOT tgisinternal
                """
            ).fetchone()
            self.assertEqual(trigger, ("reconciliation_run_scope_guard",))

            columns = {
                row[0]: (row[1], row[2])
                for row in connection.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'accounting_core'
                      AND table_name = 'reconciliation_run'
                    """
                ).fetchall()
            }
            for column_name in (
                "tenant_account_id",
                "legal_entity_id",
                "accounting_book_id",
                "bank_account_assignment_id",
                "currency_code",
                "bank_cutoff_at",
                "book_cutoff_at",
                "matching_policy_version",
                "knowledge_cutoff_at",
                "run_status_code",
                "recorded_at",
            ):
                self.assertIn(column_name, columns)
                self.assertEqual(columns[column_name][1], "NO")


if __name__ == "__main__":
    unittest.main()
