"""Real PostgreSQL contract for tenant isolation of lifecycle session leases."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class ReconciliationLifecycleSessionLeaseRlsPostgresTests(unittest.TestCase):
    """Keep the lifecycle lease relation inside the tenant RLS boundary."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def test_lifecycle_session_lease_forces_tenant_rls(self) -> None:
        """The authority-bearing lease table must force the canonical tenant policy."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            security = connection.execute(
                """
                SELECT relation.relrowsecurity, relation.relforcerowsecurity
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'accounting_core'
                  AND relation.relname = 'reconciliation_lifecycle_session_lease'
                """
            ).fetchone()
            self.assertEqual(security, (True, True))

            policy = connection.execute(
                """
                SELECT qual, with_check
                FROM pg_catalog.pg_policies
                WHERE schemaname = 'accounting_core'
                  AND tablename = 'reconciliation_lifecycle_session_lease'
                  AND policyname = 'reconciliation_lifecycle_session_lease_isolation'
                """
            ).fetchone()
            self.assertIsNotNone(policy)
            assert policy is not None
            self.assertIn("tenant_account_id", policy[0])
            self.assertIn("current_tenant_account_id()", policy[0])
            self.assertIn("tenant_account_id", policy[1])
            self.assertIn("current_tenant_account_id()", policy[1])


if __name__ == "__main__":
    unittest.main()
