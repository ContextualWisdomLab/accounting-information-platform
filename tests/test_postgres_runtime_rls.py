"""Real PostgreSQL regressions for forced RLS and the restricted runtime identity."""

from __future__ import annotations

import uuid
import unittest

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from accounting_information_platform import PostgresPostingLedger
from tests import test_postgres_posting as posting


_RLS_TABLES = (
    ("accounting_core", "legal_entity_record"),
    ("accounting_core", "accounting_book"),
    ("accounting_core", "chart_account"),
    ("accounting_core", "account_role_mapping"),
    ("accounting_core", "fiscal_calendar"),
    ("accounting_core", "fiscal_period"),
    ("accounting_integration", "journal_proposal_record"),
    ("accounting_core", "general_journal"),
    ("accounting_core", "journal_entry_line"),
    ("accounting_core", "journal_source_reference"),
    ("accounting_core", "journal_reversal"),
    ("accounting_integration", "posting_receipt"),
    ("accounting_reporting", "trial_balance_snapshot"),
    ("accounting_reporting", "trial_balance_line"),
    ("accounting_integration", "outbox_event"),
    ("accounting_integration", "home_tax_submission"),
)


class PostgresRuntimeRlsTests(unittest.TestCase):
    """Prove tenant RLS under a real non-owner, non-bypass application login."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_authoritative_tenant_tables_force_row_level_security(self) -> None:
        """Every tenant-scoped accounting fact table forces its checked-in RLS policy."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            for schema_name, table_name in _RLS_TABLES:
                with self.subTest(table=f"{schema_name}.{table_name}"):
                    row = connection.execute(
                        """
                        SELECT pg_class.relrowsecurity, pg_class.relforcerowsecurity
                        FROM pg_class
                        JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                        WHERE pg_namespace.nspname = %s
                          AND pg_class.relname = %s
                        """,
                        (schema_name, table_name),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    assert row is not None
                    self.assertEqual(row, (True, True))

    def test_restricted_runtime_login_posts_same_tenant_and_cannot_read_other_tenant(self) -> None:
        """A least-privilege runtime can post its tenant but RLS hides another tenant."""
        other = posting.PostgresPostingTests("setUp")
        other.setUp()
        self.addCleanup(other.doCleanups)
        self.addCleanup(other.tearDown)
        other.ledger.post(other._two_line_proposal(), other.policy)

        role_name = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        password = f"AisRuntime{uuid.uuid4().hex}"
        self._create_runtime_role(role_name, password)
        self.addCleanup(self._drop_runtime_role, role_name)
        runtime_url = self._runtime_database_url(role_name, password)
        runtime_ledger = PostgresPostingLedger(runtime_url, self.case.policy.tenant_reference)

        receipt = runtime_ledger.post(self.case._two_line_proposal(), self.case.policy)
        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(runtime_ledger.journal_count, 1)

        with psycopg.connect(runtime_url) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.case.tenant_id,),
            )
            own_count = connection.execute(
                "SELECT count(*) FROM accounting_core.general_journal"
            ).fetchone()[0]
            other_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s
                """,
                (other.tenant_id,),
            ).fetchone()[0]
        self.assertEqual(own_count, 1)
        self.assertEqual(other_count, 0)

        with psycopg.connect(posting.DATABASE_URL) as admin:
            role_row = admin.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
                (role_name,),
            ).fetchone()
            owner_name = admin.execute(
                """
                SELECT pg_get_userbyid(pg_class.relowner)
                FROM pg_class
                JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                WHERE pg_namespace.nspname = 'accounting_core'
                  AND pg_class.relname = 'general_journal'
                """
            ).fetchone()[0]
        self.assertEqual(role_row, (False, False))
        self.assertNotEqual(owner_name, role_name)

    @staticmethod
    def _runtime_database_url(role_name: str, password: str) -> str:
        """Return the CI database DSN with only the runtime login replaced."""
        settings = conninfo_to_dict(posting.DATABASE_URL)
        settings["user"] = role_name
        settings["password"] = password
        return make_conninfo(**settings)

    @staticmethod
    def _create_runtime_role(role_name: str, password: str) -> None:
        """Provision a test-only non-owner runtime with the current posting privileges."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role_name), sql.Literal(password))
            )
            admin.execute(
                sql.SQL(
                    "GRANT USAGE ON SCHEMA accounting_core, accounting_integration, accounting_reporting TO {}"
                ).format(sql.Identifier(role_name))
            )
            for schema_name in (
                "accounting_core",
                "accounting_integration",
                "accounting_reporting",
            ):
                admin.execute(
                    sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
            for schema_name, table_name in (
                ("accounting_integration", "journal_proposal_record"),
                ("accounting_core", "general_journal"),
                ("accounting_core", "journal_entry_line"),
                ("accounting_core", "journal_source_reference"),
                ("accounting_integration", "posting_receipt"),
                ("accounting_integration", "outbox_event"),
            ):
                admin.execute(
                    sql.SQL("GRANT INSERT ON {}.{} TO {}").format(
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                        sql.Identifier(role_name),
                    )
                )

    @staticmethod
    def _drop_runtime_role(role_name: str) -> None:
        """Remove the test-only runtime identity even after a failed assertion."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role_name))
            )
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))


if __name__ == "__main__":
    unittest.main()
