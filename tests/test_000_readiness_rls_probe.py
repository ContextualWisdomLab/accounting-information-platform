"""Temporary PostgreSQL 18 probe for canonical readiness isolation metadata."""

from __future__ import annotations

import hashlib
import json
import unittest

import psycopg

from tests import test_postgres_posting as posting


class TemporaryReadinessIsolationProbe(unittest.TestCase):
    """Emit compact canonical RLS, policy, and tenant-function evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete checked-in migration chain before probing catalogs."""
        posting.PostgresPostingTests.setUpClass()

    def test_emit_canonical_isolation_metadata(self) -> None:
        """Fail intentionally with deterministic PostgreSQL 18 isolation fingerprints."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rls_rows = connection.execute(
                """
                SELECT namespace.nspname,
                       relation.relname,
                       relation.relrowsecurity,
                       relation.relforcerowsecurity
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname IN (
                    'accounting_core', 'accounting_integration', 'accounting_reporting'
                )
                  AND relation.relkind IN ('r', 'p')
                ORDER BY namespace.nspname, relation.relname
                """
            ).fetchall()
            policy_rows = connection.execute(
                """
                SELECT schemaname,
                       tablename,
                       policyname,
                       permissive,
                       pg_catalog.array_to_string(roles, ','),
                       cmd,
                       COALESCE(qual, ''),
                       COALESCE(with_check, '')
                FROM pg_catalog.pg_policies
                WHERE schemaname IN (
                    'accounting_core', 'accounting_integration', 'accounting_reporting'
                )
                ORDER BY schemaname, tablename, policyname
                """
            ).fetchall()
            tenant_function = connection.execute(
                """
                SELECT pg_catalog.md5(pg_catalog.pg_get_functiondef(function.oid))
                FROM pg_catalog.pg_proc AS function
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = function.pronamespace
                WHERE namespace.nspname = 'accounting_core'
                  AND function.proname = 'current_tenant_account_id'
                  AND pg_catalog.pg_get_function_identity_arguments(function.oid) = ''
                """
            ).fetchone()
        rls_payload = [list(row) for row in rls_rows]
        policy_payload = [list(row) for row in policy_rows]
        evidence = {
            "rls_count": len(rls_payload),
            "rls_sha256": hashlib.sha256(
                json.dumps(rls_payload, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "rls_enabled_forced": [
                [schema_name, table_name]
                for schema_name, table_name, enabled, forced in rls_rows
                if enabled and forced
            ],
            "policy_count": len(policy_payload),
            "policy_sha256": hashlib.sha256(
                json.dumps(policy_payload, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "tenant_function_md5": tenant_function[0] if tenant_function else "",
        }
        self.fail("CANONICAL_ISOLATION=" + json.dumps(evidence, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
