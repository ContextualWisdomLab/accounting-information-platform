"""RED contracts for durable many-to-many reconciliation allocation evidence."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0014_reconciliation_match_allocation.sql"


class ReconciliationAllocationPersistenceRedTests(unittest.TestCase):
    """Require normalized append-only allocation evidence before M2 is complete."""

    def test_migration_defines_match_and_both_allocation_sides(self) -> None:
        """Durable proposals use normalized rows rather than an opaque allocation blob."""
        self.assertTrue(
            MIGRATION.exists(),
            "Add migration 0014 before treating in-memory allocation plans as durable reconciliation evidence.",
        )
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for object_name in (
            "reconciliation_match",
            "statement_match_allocation",
            "journal_match_allocation",
        ):
            self.assertIn(f"create table accounting_core.{object_name}", normalized)

        self.assertNotIn("jsonb", normalized)
        self.assertIn("allocated_amount numeric(38, 6) not null", normalized)
        self.assertIn("check (allocated_amount > 0)", normalized)
        self.assertIn("currency_code text not null", normalized)
        self.assertIn("matching_rule_code text not null", normalized)

    def test_allocation_rows_are_bound_to_one_tenant_and_run(self) -> None:
        """Statement and journal allocations cannot be relabelled into another run."""
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for table_name in ("statement_match_allocation", "journal_match_allocation"):
            self.assertIn(
                f"foreign key (tenant_account_id, reconciliation_run_id, reconciliation_match_id) references accounting_core.reconciliation_match (tenant_account_id, reconciliation_run_id, reconciliation_match_id)",
                normalized,
                f"{table_name} must bind the allocation to the same tenant/run as its match",
            )

        self.assertIn(
            "foreign key (tenant_account_id, bank_statement_entry_id) references accounting_integration.bank_statement_entry (tenant_account_id, bank_statement_entry_id)",
            normalized,
        )
        self.assertIn(
            "foreign key (tenant_account_id, general_journal_id) references accounting_core.general_journal (tenant_account_id, general_journal_id)",
            normalized,
        )

    def test_allocation_evidence_is_forced_rls_and_append_only(self) -> None:
        """Recorded proposal evidence is tenant isolated and corrected by superseding evidence."""
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for object_name in (
            "reconciliation_match",
            "statement_match_allocation",
            "journal_match_allocation",
        ):
            self.assertIn(
                f"alter table accounting_core.{object_name} force row level security",
                normalized,
            )
        self.assertIn("reject_reconciliation_allocation_mutation", normalized)
        self.assertIn("before update or delete on accounting_core.reconciliation_match", normalized)
        self.assertIn("before update or delete on accounting_core.statement_match_allocation", normalized)
        self.assertIn("before update or delete on accounting_core.journal_match_allocation", normalized)


@unittest.skipUnless(MIGRATION.exists(), "RED until durable allocation migration exists")
class PostgresReconciliationAllocationPersistenceTests(unittest.TestCase):
    """Verify allocation isolation and deferred conservation on PostgreSQL 18."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def test_allocation_tables_force_rls(self) -> None:
        """Every durable match/allocation relation is forced through tenant RLS."""
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
                    "journal_match_allocation",
                    "reconciliation_match",
                    "statement_match_allocation",
                ],),
            ).fetchall()
        self.assertEqual(
            rows,
            [
                ("journal_match_allocation", True, True),
                ("reconciliation_match", True, True),
                ("statement_match_allocation", True, True),
            ],
        )

    def test_conservation_guards_are_deferred_constraint_triggers(self) -> None:
        """Partial match writes can exist inside one transaction but not at commit."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT c.relname, t.tgname, t.tgdeferrable, t.tginitdeferred
                FROM pg_trigger AS t
                JOIN pg_class AS c ON c.oid = t.tgrelid
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'accounting_core'
                  AND t.tgname = ANY(%s)
                  AND NOT t.tgisinternal
                ORDER BY c.relname, t.tgname
                """,
                ([
                    "journal_match_conservation_guard",
                    "reconciliation_match_conservation_guard",
                    "statement_match_conservation_guard",
                ],),
            ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row[2] and row[3] for row in rows))

    def test_scope_and_immutability_guards_exist(self) -> None:
        """Database guardrails own same-scope admission and append-only evidence."""
        expected = {
            "journal_match_allocation_immutable_guard",
            "journal_match_allocation_scope_guard",
            "reconciliation_match_immutable_guard",
            "statement_match_allocation_immutable_guard",
            "statement_match_allocation_scope_guard",
        }
        with psycopg.connect(posting.DATABASE_URL) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT t.tgname
                    FROM pg_trigger AS t
                    JOIN pg_class AS c ON c.oid = t.tgrelid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'accounting_core'
                      AND c.relname = ANY(%s)
                      AND NOT t.tgisinternal
                    """,
                    ([
                        "journal_match_allocation",
                        "reconciliation_match",
                        "statement_match_allocation",
                    ],),
                ).fetchall()
            }
        self.assertTrue(expected <= names)


if __name__ == "__main__":
    unittest.main()
