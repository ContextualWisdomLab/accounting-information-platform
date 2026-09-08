"""Real PostgreSQL regressions for forced RLS and the restricted runtime identity."""

from __future__ import annotations

import uuid
import unittest

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from accounting_information_platform import AccountingValidationError, PostgresPostingLedger
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
    ("accounting_core", "bank_account_record"),
    ("accounting_core", "bank_account_assignment"),
    ("accounting_integration", "bank_statement_artifact"),
    ("accounting_integration", "bank_statement_record"),
    ("accounting_integration", "bank_statement_balance"),
    ("accounting_integration", "bank_statement_entry"),
    ("accounting_integration", "bank_statement_entry_detail"),
    ("accounting_core", "reconciliation_run"),
    ("accounting_core", "reconciliation_exception"),
    ("accounting_core", "reconciliation_evidence"),
    ("accounting_core", "reconciliation_candidate"),
    ("accounting_core", "reconciliation_match"),
    ("accounting_core", "statement_match_allocation"),
    ("accounting_core", "journal_match_allocation"),
    ("accounting_core", "reconciliation_approval"),
    ("accounting_core", "reconciliation_run_command"),
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

    def test_non_bypass_migration_role_finishes_upgrade_without_temporary_policies(self) -> None:
        """A non-bypass migration role sees upgrade rows only until each migration commits."""
        role_name = f"accounting_upgrade_{uuid.uuid4().hex[:10]}"
        database_name = f"accounting_upgrade_{uuid.uuid4().hex[:10]}"
        password = f"AisUpgrade{uuid.uuid4().hex}!"
        migration_url = self._database_url(database_name, role_name, password)

        try:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOREPLICATION "
                        "NOBYPASSRLS CREATEROLE PASSWORD {}"
                    ).format(sql.Identifier(role_name), sql.Literal(password))
                )
                admin.execute(
                    sql.SQL(
                        "GRANT accounting_closing_writer TO {} WITH ADMIN OPTION"
                    ).format(sql.Identifier(role_name))
                )
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database_name), sql.Identifier(role_name)
                    )
                )

            posting.apply_foundation_migration(migration_url, posting.MIGRATION_PATH)

            with psycopg.connect(migration_url) as migration_connection:
                role_row = migration_connection.execute(
                    "SELECT rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user"
                ).fetchone()
                remaining_policies = migration_connection.execute(
                    """
                    SELECT policyname
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'accounting_core'
                      AND policyname IN (
                          'reconciliation_candidate_upgrade_visibility',
                          'reconciliation_run_upgrade_visibility',
                          'reconciliation_bank_account_assignment_upgrade_visibility',
                          'reconciliation_approval_upgrade_visibility'
                      )
                    ORDER BY policyname
                    """
                ).fetchall()

            self.assertEqual(role_row, (False,))
            self.assertEqual(remaining_policies, [])
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )

    def test_restricted_runtime_cannot_invoke_lifecycle_lock_helpers(self) -> None:
        """Schema usage alone never grants the SECURITY DEFINER lifecycle lock capability."""
        role_name = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        password = f"AisRuntime{uuid.uuid4().hex}"
        self._create_runtime_role(role_name, password, self.case.tenant_id)
        self.addCleanup(self._drop_runtime_role, role_name)
        runtime_url = self._runtime_database_url(role_name, password)

        for function_name in (
            "acquire_reconciliation_lifecycle_session",
            "release_reconciliation_lifecycle_session",
        ):
            with self.subTest(function=function_name):
                with psycopg.connect(runtime_url) as connection:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        connection.execute(
                            sql.SQL("SELECT accounting_core.{}(%s, %s)").format(
                                sql.Identifier(function_name)
                            ),
                            (self.case.policy.tenant_reference, uuid.uuid4()),
                        )
                    connection.rollback()

    def test_restricted_runtime_login_posts_same_tenant_and_cannot_rebind_other_tenant(self) -> None:
        """A least-privilege runtime posts its tenant and cannot self-authorize another tenant."""
        other = posting.PostgresPostingTests("setUp")
        other.setUp()
        self.addCleanup(other.doCleanups)
        self.addCleanup(other.tearDown)
        other.ledger.post(other._two_line_proposal(), other.policy)

        role_name = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        password = f"AisRuntime{uuid.uuid4().hex}"
        self._create_runtime_role(role_name, password, self.case.tenant_id)
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

            # A compromised ordinary runtime connection must not gain another
            # tenant merely by rewriting the caller-controlled custom GUC used
            # by legacy session binding. Tenant authority must be anchored in a
            # database-controlled binding to the authenticated session login.
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (other.tenant_id,),
            )
            rebound_other_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s
                """,
                (other.tenant_id,),
            ).fetchone()[0]
        self.assertEqual(own_count, 1)
        self.assertEqual(other_count, 0)
        self.assertEqual(rebound_other_count, 0)

        wrong_tenant_ledger = PostgresPostingLedger(runtime_url, other.policy.tenant_reference)
        with self.assertRaisesRegex(
            AccountingValidationError, "not provisioned for this tenant"
        ):
            _ = wrong_tenant_ledger.journal_count

        unbound_role = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        unbound_password = f"AisRuntime{uuid.uuid4().hex}"
        self._create_runtime_role(unbound_role, unbound_password, None)
        self.addCleanup(self._drop_runtime_role, unbound_role)
        unbound_url = self._runtime_database_url(unbound_role, unbound_password)
        unbound_ledger = PostgresPostingLedger(unbound_url, self.case.policy.tenant_reference)
        with self.assertRaisesRegex(
            AccountingValidationError, "cannot be authorized for the requested tenant"
        ):
            _ = unbound_ledger.journal_count

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
                """,
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
    def _database_url(database_name: str, role_name: str, password: str) -> str:
        """Return the CI database DSN with the selected login and database."""
        settings = conninfo_to_dict(posting.DATABASE_URL)
        settings["dbname"] = database_name
        settings["user"] = role_name
        settings["password"] = password
        return make_conninfo(**settings)

    @staticmethod
    def _create_runtime_role(role_name: str, password: str, tenant_id: object | None) -> None:
        """Provision a test-only runtime and optionally bind its authenticated login to a tenant."""
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
            admin.execute(
                sql.SQL("REVOKE ALL ON accounting_core.runtime_tenant_binding FROM {}").format(
                    sql.Identifier(role_name)
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
            if tenant_id is not None:
                role_oid = admin.execute(
                    "SELECT oid FROM pg_catalog.pg_roles WHERE rolname = %s",
                    (role_name,),
                ).fetchone()[0]
                admin.execute(
                    """
                    INSERT INTO accounting_core.runtime_tenant_binding (
                        runtime_role_oid,
                        runtime_role_name,
                        tenant_account_id
                    ) VALUES (%s, %s, %s)
                    """,
                    (role_oid, role_name, tenant_id),
                )

    @staticmethod
    def _drop_runtime_role(role_name: str) -> None:
        """Remove the test-only runtime identity even after a failed assertion."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                "DELETE FROM accounting_core.runtime_tenant_binding WHERE runtime_role_name = %s",
                (role_name,),
            )
            admin.execute(
                sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role_name))
            )
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))


if __name__ == "__main__":
    unittest.main()
