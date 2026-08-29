"""Real PostgreSQL regressions for forced RLS and the restricted runtime identity."""

from __future__ import annotations

import http.client
import json
import uuid
import unittest
from threading import Thread

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from accounting_information_platform import (
    AccountingValidationError,
    PostgresPostingLedger,
    create_journal_proposal_server,
)
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
    ("accounting_integration", "bank_statement_entry"),
    ("accounting_integration", "bank_statement_entry_detail"),
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

    def test_readiness_requires_a_database_tenant_binding(self) -> None:
        """Readiness rejects an unbound privileged session and accepts a bound runtime."""
        with self.assertRaisesRegex(
            AccountingValidationError, "cannot be authorized for the requested tenant"
        ):
            PostgresPostingLedger(
                posting.DATABASE_URL, self.case.policy.tenant_reference
            ).check_readiness()

        role_name = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        password = f"AisRuntime{uuid.uuid4().hex}"
        self._create_runtime_role(role_name, password, self.case.tenant_id)
        self.addCleanup(self._drop_runtime_role, role_name)
        runtime_ledger = PostgresPostingLedger(
            self._runtime_database_url(role_name, password),
            self.case.policy.tenant_reference,
        )

        runtime_ledger.check_readiness()

        server = create_journal_proposal_server(
            self._runtime_database_url(role_name, password),
            self.case.policy.tenant_reference,
            "127.0.0.1",
            0,
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", "/readyz")
            response = connection.getresponse()
            body = json.loads(response.read())
        finally:
            connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(body, {"status": "ready"})

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
