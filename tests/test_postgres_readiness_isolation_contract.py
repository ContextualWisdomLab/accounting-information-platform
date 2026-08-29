"""Real PostgreSQL regressions for readiness tenant-isolation contracts."""

from __future__ import annotations

import http.client
import time
import unittest
import uuid
from threading import Thread

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from accounting_information_platform import (
    AccountingValidationError,
    PostgresPostingLedger,
    create_journal_proposal_server,
)
from tests import test_postgres_posting as posting
from tests import test_postgres_runtime_rls as runtime_rls


_EXPECTED_RLS_POLICIES = (
    ("accounting_core", "account_role_mapping", "account_mapping_isolation"),
    ("accounting_core", "accounting_book", "accounting_book_isolation"),
    (
        "accounting_core",
        "accounting_book_period_control",
        "accounting_book_period_isolation",
    ),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_isolation"),
    ("accounting_core", "bank_account_record", "bank_account_record_isolation"),
    ("accounting_core", "chart_account", "chart_account_isolation"),
    ("accounting_core", "fiscal_calendar", "fiscal_calendar_isolation"),
    ("accounting_core", "fiscal_period", "fiscal_period_isolation"),
    ("accounting_core", "general_journal", "general_journal_isolation"),
    ("accounting_core", "journal_entry_line", "journal_entry_isolation"),
    ("accounting_core", "journal_match_allocation", "journal_match_allocation_isolation"),
    ("accounting_core", "journal_reversal", "journal_reversal_isolation"),
    ("accounting_core", "journal_source_reference", "journal_source_isolation"),
    ("accounting_core", "legal_entity_record", "legal_entity_isolation"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_isolation"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_isolation"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_isolation"),
    ("accounting_core", "reconciliation_match", "reconciliation_match_isolation"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_isolation"),
    ("accounting_core", "statement_match_allocation", "statement_match_allocation_isolation"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_isolation"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_isolation"),
    (
        "accounting_integration",
        "bank_statement_entry_detail",
        "bank_statement_detail_isolation",
    ),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_isolation"),
    (
        "accounting_integration",
        "fiscal_period_open_command",
        "fiscal_period_open_command_isolation",
    ),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_isolation"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_isolation"),
    ("accounting_integration", "outbox_event", "outbox_event_isolation"),
    ("accounting_integration", "posting_receipt", "posting_receipt_isolation"),
    ("accounting_reporting", "trial_balance_line", "trial_line_isolation"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_snapshot_isolation"),
)
_TENANT_FUNCTION_FINGERPRINT = "9c2cfaea74d193cadc39f46c242dd9a5"
_READINESS_TIMEOUT_SECONDS = 5


class PostgresReadinessIsolationContractTests(unittest.TestCase):
    """Prove readiness fails closed when tenant isolation or its bound query stalls."""

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
        self.addCleanup(runtime_rls.PostgresRuntimeRlsTests._drop_runtime_role, role_name)
        self.runtime_url = runtime_rls.PostgresRuntimeRlsTests._runtime_database_url(
            role_name, password
        )
        self.runtime_ledger = PostgresPostingLedger(
            self.runtime_url, self.case.policy.tenant_reference
        )

    def test_installed_isolation_contract_matches_migrations(self) -> None:
        """Every tenant-scoped fact table has exact forced RLS and policy metadata."""
        expected_tables = set(_EXPECTED_RLS_POLICIES)
        with psycopg.connect(posting.DATABASE_URL) as admin:
            actual_rls = set(
                admin.execute(
                    """
                    SELECT namespace.nspname, relation.relname
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE (namespace.nspname, relation.relname) IN (
                        SELECT * FROM unnest(%s::text[], %s::text[])
                    )
                      AND relation.relrowsecurity
                      AND relation.relforcerowsecurity
                    """,
                    (
                        [schema_name for schema_name, _table_name, _policy_name in _EXPECTED_RLS_POLICIES],
                        [table_name for _schema_name, table_name, _policy_name in _EXPECTED_RLS_POLICIES],
                    ),
                ).fetchall()
            )
            actual_policies = set(
                admin.execute(
                    """
                    SELECT schemaname, tablename, policyname
                    FROM pg_catalog.pg_policies
                    WHERE schemaname IN ('accounting_core', 'accounting_integration', 'accounting_reporting')
                    """
                ).fetchall()
            )
            tenant_function = admin.execute(
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
        self.assertEqual(actual_rls, {(schema_name, table_name) for schema_name, table_name, _ in expected_tables})
        self.assertEqual(actual_policies, expected_tables)
        self.assertEqual(tenant_function, (_TENANT_FUNCTION_FINGERPRINT,))

    def test_readiness_rejects_disabled_row_level_security(self) -> None:
        """Disabling forced RLS on an authoritative table makes readiness fail closed."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                "ALTER TABLE accounting_core.general_journal DISABLE ROW LEVEL SECURITY"
            )
        try:
            with self.assertRaisesRegex(
                AccountingValidationError, "accounting database schema is incomplete"
            ):
                self.runtime_ledger.check_readiness()
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    "ALTER TABLE accounting_core.general_journal ENABLE ROW LEVEL SECURITY"
                )
                admin.execute(
                    "ALTER TABLE accounting_core.general_journal FORCE ROW LEVEL SECURITY"
                )

    def test_readiness_rejects_policy_definition_drift(self) -> None:
        """Dropping an exact tenant policy makes readiness fail closed."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                "DROP POLICY general_journal_isolation ON accounting_core.general_journal"
            )
        try:
            with self.assertRaisesRegex(
                AccountingValidationError, "accounting database schema is incomplete"
            ):
                self.runtime_ledger.check_readiness()
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    """
                    CREATE POLICY general_journal_isolation
                    ON accounting_core.general_journal
                    USING (tenant_account_id = accounting_core.current_tenant_account_id())
                    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id())
                    """
                )

    def test_readyz_returns_503_when_a_connected_readiness_query_times_out(self) -> None:
        """A connected tenant lookup cannot retain an HTTP worker beyond the bound."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            canonical_definition = admin.execute(
                """
                SELECT pg_catalog.pg_get_functiondef(
                    'accounting_core.current_tenant_account_id()'::regprocedure
                )
                """
            ).fetchone()[0]
            admin.execute(
                """
                CREATE OR REPLACE FUNCTION accounting_core.current_tenant_account_id()
                RETURNS uuid
                LANGUAGE plpgsql
                STABLE
                SECURITY DEFINER
                SET search_path = pg_catalog, accounting_core
                AS $drift$
                BEGIN
                    PERFORM pg_catalog.pg_sleep(10);
                    RETURN NULL;
                END;
                $drift$
                """
            )
        server = create_journal_proposal_server(
            self.runtime_url,
            self.case.policy.tenant_reference,
            "127.0.0.1",
            0,
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=_READINESS_TIMEOUT_SECONDS + 3
        )
        started = time.monotonic()
        try:
            connection.request("GET", "/readyz")
            response = connection.getresponse()
            body = response.read()
            elapsed = time.monotonic() - started
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(canonical_definition)
        self.assertEqual(response.status, 503)
        self.assertLess(elapsed, _READINESS_TIMEOUT_SECONDS + 2)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIn(b'"status":"not_ready"', body)

    def test_readiness_preserves_a_stricter_configured_statement_timeout(self) -> None:
        """A deployment timeout below the readiness cap remains effective."""
        settings = conninfo_to_dict(self.runtime_url)
        settings["options"] = "-c statement_timeout=100ms"
        strict_ledger = PostgresPostingLedger(
            make_conninfo(**settings), self.case.policy.tenant_reference
        )
        with strict_ledger._session(readiness=True) as connection:
            self.assertEqual(
                connection.execute("SHOW statement_timeout").fetchone()[0], "100ms"
            )


if __name__ == "__main__":
    unittest.main()
