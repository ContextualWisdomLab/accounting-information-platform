"""PostgreSQL readiness regressions for stored control definitions and indexes."""

from __future__ import annotations

from pathlib import Path
import re
import unittest
import uuid

import psycopg
from psycopg import sql

from accounting_information_platform import AccountingValidationError, PostgresPostingLedger
from accounting_information_platform import persistence as persistence_module
from tests import test_postgres_posting as posting
from tests import test_postgres_runtime_rls as runtime_rls


_CONTROL_FUNCTIONS = (
    ("accounting_core", "guard_journal_line_book_scope"),
    ("accounting_core", "guard_period_insert"),
    ("accounting_core", "guard_reversal_temporal_order"),
    ("accounting_core", "guard_reversal_lineage_insert"),
    ("accounting_core", "reject_finalized_fact_mutation"),
    ("accounting_core", "guard_finalized_journal_extension"),
    ("accounting_integration", "reject_period_open_command_mutation"),
    ("accounting_core", "guard_soft_close_evidence_update"),
    ("accounting_integration", "reject_statement_mutation"),
    ("accounting_core", "reject_reconciliation_run_scope_mutation"),
)

_EXPLICIT_INDEX_PATTERN = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?P<index>[a-z0-9_]+)\s+"
    r"ON\s+(?P<schema>[a-z0-9_]+)\.[a-z0-9_]+",
    re.IGNORECASE,
)

_EXPLICIT_TRIGGER_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+"
    r"(?P<trigger>[a-z0-9_]+)\b",
    re.IGNORECASE,
)


class PostgresReadinessDefinitionContractTests(unittest.TestCase):
    """Reject same-identity control rewrites and missing checked-in indexes."""

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

    def test_same_signature_control_function_rewrites_fail_readiness(self) -> None:
        """Replacing any behavior-defining trigger function with a no-op fails closed."""
        self.runtime_ledger.check_readiness()
        for schema_name, function_name in _CONTROL_FUNCTIONS:
            with self.subTest(function_name=f"{schema_name}.{function_name}"):
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                    canonical_definition = admin.execute(
                        """
                        SELECT pg_get_functiondef(
                            (%s || '.' || %s || '()')::regprocedure
                        )
                        """,
                        (schema_name, function_name),
                    ).fetchone()[0]
                    admin.execute(
                        f"""
                        CREATE OR REPLACE FUNCTION {schema_name}.{function_name}()
                        RETURNS trigger
                        LANGUAGE plpgsql
                        AS $drift$
                        BEGIN
                            RETURN NULL;
                        END;
                        $drift$
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
                        admin.execute(canonical_definition)
                self.runtime_ledger.check_readiness()

    def test_every_checked_in_explicit_index_is_required_by_readiness(self) -> None:
        """The readiness index inventory cannot lag an explicit migration index."""
        migration_directory = Path("database/migrations")
        expected_indexes: set[str] = set()
        for migration_path in sorted(migration_directory.glob("*.sql")):
            for match in _EXPLICIT_INDEX_PATTERN.finditer(
                migration_path.read_text(encoding="utf-8")
            ):
                expected_indexes.add(
                    f"{match.group('schema').lower()}.{match.group('index').lower()}"
                )
        self.assertTrue(expected_indexes)
        self.assertEqual(expected_indexes, set(persistence_module._READINESS_INDEXES))

    def test_every_checked_in_trigger_is_required_by_readiness(self) -> None:
        """The readiness trigger inventory cannot silently lag a migration trigger."""
        migration_directory = Path("database/migrations")
        expected_triggers: set[str] = set()
        for migration_path in sorted(migration_directory.glob("*.sql")):
            for match in _EXPLICIT_TRIGGER_PATTERN.finditer(
                migration_path.read_text(encoding="utf-8")
            ):
                expected_triggers.add(match.group("trigger").lower())
        required_triggers = {
            item[2] for item in persistence_module._READINESS_BALANCE_TRIGGERS
        } | {item[2] for item in persistence_module._READINESS_CONTROL_TRIGGERS}
        self.assertTrue(expected_triggers)
        self.assertEqual(expected_triggers, required_triggers)

    def test_required_index_drop_fails_readiness(self) -> None:
        """Dropping any explicitly checked-in index makes readiness fail closed."""
        self.runtime_ledger.check_readiness()
        for index_name in persistence_module._READINESS_INDEXES:
            with self.subTest(index_name=index_name):
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                    canonical_definition = admin.execute(
                        "SELECT pg_get_indexdef(%s::regclass)",
                        (index_name,),
                    ).fetchone()[0]
                    admin.execute(f"DROP INDEX {index_name}")
                try:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "accounting database schema is incomplete",
                    ):
                        self.runtime_ledger.check_readiness()
                finally:
                    with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                        admin.execute(canonical_definition)
                self.runtime_ledger.check_readiness()

    def test_same_name_weakened_constraint_fails_readiness(self) -> None:
        """A same-name constraint with weaker semantics must not satisfy readiness."""
        schema_name = "accounting_core"
        table_name = "accounting_book_period_control"
        constraint_name = "soft_close_evidence_complete_check"
        self.runtime_ledger.check_readiness()
        constraint_dropped = False
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            canonical_definition = admin.execute(
                """
                SELECT pg_get_constraintdef(constraint.oid, true)
                FROM pg_catalog.pg_constraint AS constraint
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = constraint.conrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = %s
                  AND relation.relname = %s
                  AND constraint.conname = %s
                """,
                (schema_name, table_name, constraint_name),
            ).fetchone()[0]
        try:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("ALTER TABLE {}.{} DROP CONSTRAINT {}").format(
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                        sql.Identifier(constraint_name),
                    )
                )
                constraint_dropped = True
                admin.execute(
                    sql.SQL(
                        "ALTER TABLE {}.{} ADD CONSTRAINT {} CHECK (true) NOT VALID"
                    ).format(
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                        sql.Identifier(constraint_name),
                    )
                )
            with self.assertRaisesRegex(
                AccountingValidationError,
                "accounting database schema is incomplete",
            ):
                self.runtime_ledger.check_readiness()
        finally:
            if constraint_dropped:
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                    admin.execute(
                        sql.SQL("ALTER TABLE {}.{} DROP CONSTRAINT IF EXISTS {}").format(
                            sql.Identifier(schema_name),
                            sql.Identifier(table_name),
                            sql.Identifier(constraint_name),
                        )
                    )
                    admin.execute(
                        sql.SQL("ALTER TABLE {}.{} ADD CONSTRAINT {} {}").format(
                            sql.Identifier(schema_name),
                            sql.Identifier(table_name),
                            sql.Identifier(constraint_name),
                            sql.SQL(canonical_definition),
                        )
                    )
        self.runtime_ledger.check_readiness()

    def test_same_name_weakened_index_fails_readiness(self) -> None:
        """A same-name index on a different expression must not satisfy readiness."""
        index_name = "accounting_integration.home_tax_submission_scope_order_index"
        self.runtime_ledger.check_readiness()
        index_dropped = False
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            canonical_definition, schema_name, table_name = admin.execute(
                """
                SELECT pg_get_indexdef(index_relation.oid),
                       table_namespace.nspname,
                       table_relation.relname
                FROM pg_catalog.pg_class AS index_relation
                JOIN pg_catalog.pg_index AS index_metadata
                  ON index_metadata.indexrelid = index_relation.oid
                JOIN pg_catalog.pg_class AS table_relation
                  ON table_relation.oid = index_metadata.indrelid
                JOIN pg_catalog.pg_namespace AS table_namespace
                  ON table_namespace.oid = table_relation.relnamespace
                WHERE index_relation.oid = %s::regclass
                """,
                (index_name,),
            ).fetchone()
        try:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP INDEX {}").format(sql.SQL(index_name)))
                index_dropped = True
                index_schema, index_relation_name = index_name.split(".", 1)
                admin.execute(
                    sql.SQL("CREATE INDEX {}.{} ON {}.{} ((1))").format(
                        sql.Identifier(index_schema),
                        sql.Identifier(index_relation_name),
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                    )
                )
            with self.assertRaisesRegex(
                AccountingValidationError,
                "accounting database schema is incomplete",
            ):
                self.runtime_ledger.check_readiness()
        finally:
            if index_dropped:
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                    admin.execute(
                        sql.SQL("DROP INDEX IF EXISTS {}").format(sql.SQL(index_name))
                    )
                    admin.execute(canonical_definition)
        self.runtime_ledger.check_readiness()


if __name__ == "__main__":
    unittest.main()
