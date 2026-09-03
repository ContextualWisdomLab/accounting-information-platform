"""PostgreSQL readiness regressions for complete runtime column semantics."""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from collections import defaultdict

import psycopg

from accounting_information_platform import AccountingValidationError, PostgresPostingLedger
from accounting_information_platform import persistence as persistence_module
from tests import test_postgres_posting as posting
from tests import test_postgres_runtime_rls as runtime_rls


def _installed_column_fingerprints() -> dict[tuple[str, str], tuple[int, str]]:
    """Return exact PostgreSQL 18 fingerprints for every authoritative table's columns."""
    with psycopg.connect(posting.DATABASE_URL) as admin:
        rows = admin.execute(
            """
            SELECT namespace.nspname,
                   relation.relname,
                   attribute.attname,
                   pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                   attribute.attnotnull,
                   COALESCE(
                       pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid),
                       ''
                   ),
                   attribute.attidentity::text,
                   attribute.attgenerated::text,
                   COALESCE(
                       collation_namespace.nspname || '.' || collation_row.collname,
                       ''
                   )
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid = relation.oid
             AND default_value.adnum = attribute.attnum
            LEFT JOIN pg_catalog.pg_collation AS collation_row
              ON collation_row.oid = attribute.attcollation
             AND attribute.attcollation <> 0
            LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
              ON collation_namespace.oid = collation_row.collnamespace
            WHERE namespace.nspname IN (
                'accounting_core', 'accounting_integration', 'accounting_reporting'
            )
              AND relation.relkind IN ('r', 'p')
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            ORDER BY namespace.nspname, relation.relname, attribute.attnum
            """
        ).fetchall()
    grouped: dict[tuple[str, str], list[list[object]]] = defaultdict(list)
    for row in rows:
        grouped[(row[0], row[1])].append(list(row[2:]))
    return {
        table_key: (
            len(metadata),
            hashlib.sha256(
                json.dumps(metadata, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )
        for table_key, metadata in grouped.items()
    }


class PostgresReadinessColumnContractTests(unittest.TestCase):
    """Fail readiness when authoritative table column semantics drift."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in PostgreSQL foundation once for this regression module."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one tenant and provision a restricted runtime identity for readiness."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        role_name = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        password = f"AisRuntime{uuid.uuid4().hex}"
        runtime_rls.PostgresRuntimeRlsTests._create_runtime_role(
            role_name, password, self.case.tenant_id
        )
        self.addCleanup(
            runtime_rls.PostgresRuntimeRlsTests._drop_runtime_role,
            role_name,
        )
        self.runtime_ledger = PostgresPostingLedger(
            runtime_rls.PostgresRuntimeRlsTests._runtime_database_url(
                role_name, password
            ),
            self.case.policy.tenant_reference,
        )

    def test_installed_column_inventory_matches_readiness(self) -> None:
        """Every authoritative table binds ordered type/null/default/collation semantics."""
        expected = {
            (schema_name, table_name): (column_count, fingerprint)
            for schema_name, table_name, column_count, fingerprint in (
                persistence_module._READINESS_COLUMN_FINGERPRINTS
            )
        }
        self.assertEqual(
            {tuple(table_name.split(".", 1)) for table_name in persistence_module._READINESS_TABLES},
            set(expected),
        )
        self.assertEqual(_installed_column_fingerprints(), expected)

    def test_additive_schema_objects_do_not_fail_readiness(self) -> None:
        """Compatible tables and columns added by a migration remain serviceable."""
        self.runtime_ledger.check_readiness()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                """
                CREATE TABLE accounting_core.readiness_additive_table (
                    readiness_additive_value integer
                )
                """
            )
            admin.execute(
                """
                ALTER TABLE accounting_core.general_journal
                ADD COLUMN readiness_additive_column text
                """
            )
        try:
            self.runtime_ledger.check_readiness()
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    """
                    ALTER TABLE accounting_core.general_journal
                    DROP COLUMN readiness_additive_column
                    """
                )
                admin.execute(
                    "DROP TABLE accounting_core.readiness_additive_table"
                )
        self.runtime_ledger.check_readiness()

    def test_material_default_drift_fails_readiness(self) -> None:
        """A same-column default rewrite must make the runtime readiness probe fail closed."""
        self.runtime_ledger.check_readiness()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                """
                ALTER TABLE accounting_core.general_journal
                ALTER COLUMN posted_at SET DEFAULT statement_timestamp()
                """
            )
        try:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "accounting database schema is incomplete",
            ):
                self.runtime_ledger.check_readiness()
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    """
                    ALTER TABLE accounting_core.general_journal
                    ALTER COLUMN posted_at SET DEFAULT clock_timestamp()
                    """
                )
        self.runtime_ledger.check_readiness()


if __name__ == "__main__":
    unittest.main()
