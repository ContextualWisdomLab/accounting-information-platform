"""Real PostgreSQL contract for retained reconciliation-evidence scope identity."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class ReconciliationExceptionResolutionScopeFkPostgresTests(unittest.TestCase):
    """Bind exception-resolution evidence to tenant, run, and exception in the schema."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def test_resolution_evidence_foreign_key_carries_complete_scope(self) -> None:
        """The command FK must not rely on a globally unique evidence UUID alone."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            unique_constraint = connection.execute(
                """
                SELECT array_length(constraint_row.conkey, 1)
                FROM pg_catalog.pg_constraint AS constraint_row
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = constraint_row.conrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'accounting_core'
                  AND relation.relname = 'reconciliation_evidence'
                  AND constraint_row.conname = 'reconciliation_evidence_scope_identity'
                  AND constraint_row.contype = 'u'
                """
            ).fetchone()
            self.assertEqual(unique_constraint, (4,))

            foreign_key = connection.execute(
                """
                SELECT array_length(constraint_row.conkey, 1),
                       array_length(constraint_row.confkey, 1),
                       constraint_row.confrelid::regclass::text
                FROM pg_catalog.pg_constraint AS constraint_row
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = constraint_row.conrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'accounting_core'
                  AND relation.relname = 'reconciliation_exception_resolution_command'
                  AND constraint_row.conname = 'reconciliation_exception_resolution_evidence_scope_fk'
                  AND constraint_row.contype = 'f'
                """
            ).fetchone()
            self.assertEqual(
                foreign_key,
                (4, 4, "accounting_core.reconciliation_evidence"),
            )


if __name__ == "__main__":
    unittest.main()
