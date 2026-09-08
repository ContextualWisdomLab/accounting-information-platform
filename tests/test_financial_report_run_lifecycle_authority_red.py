"""Real PostgreSQL regression for financial-report run lifecycle authority."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from tests import test_postgres_financial_report_source_registry as registry
from tests import test_postgres_posting as posting


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REGISTRY_MIGRATION = (
    ROOT / "database/migrations/0020_financial_report_source_registry.sql"
)


class FinancialReportRunLifecycleAuthorityTests(unittest.TestCase):
    """Keep report lifecycle state immutable until a purpose-bound command owns it."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the protected accounting foundation for real PostgreSQL tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one tenant, book, period, and lawful book-period control."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            registry.materialize_seeded_book_period_control(connection, self.case)

    def test_direct_sql_cannot_supersede_report_run_without_command_evidence(self) -> None:
        """Raw UPDATE cannot create report lifecycle authority without its command boundary."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._apply_registry_inside_transaction(connection)
            tenant_id, legal_entity_id, book_id, period_id = self._accounting_scope(connection)
            run_id = connection.execute(
                """
                INSERT INTO accounting_reporting.financial_report_run (
                    tenant_account_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    reporting_currency_code,
                    source_period_status_code,
                    knowledge_cutoff_at,
                    report_purpose_code,
                    run_status_code
                ) VALUES (%s, %s, %s, %s, 'USD', 'open', %s, 'integration_test', 'superseded')
                RETURNING financial_report_run_id, run_status_code
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    datetime(2000, 1, 1, tzinfo=timezone.utc),
                ),
            ).fetchone()
            assert run_id is not None
            financial_report_run_id, initial_status = run_id
            self.assertEqual(initial_status, "collecting_sources")

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "financial_report_run_lifecycle_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_reporting.financial_report_run
                       SET run_status_code = 'superseded'
                     WHERE tenant_account_id = %s
                       AND financial_report_run_id = %s
                    """,
                    (tenant_id, financial_report_run_id),
                )

    @staticmethod
    def _apply_registry_inside_transaction(connection: psycopg.Connection) -> None:
        """Execute migration 0020 without its outer transaction for rollback-safe tests."""
        migration = SOURCE_REGISTRY_MIGRATION.read_text(encoding="utf-8")
        statements = migration.removeprefix("BEGIN;\n").removesuffix("\nCOMMIT;\n")
        connection.execute(statements)

    def _accounting_scope(
        self,
        connection: psycopg.Connection,
    ) -> tuple[object, object, object, object]:
        """Return the seeded tenant, entity, book, and period identifiers."""
        row = connection.execute(
            """
            SELECT tenant_account.tenant_account_id,
                   legal_entity_record.legal_entity_id,
                   accounting_book.accounting_book_id,
                   fiscal_period.fiscal_period_id
              FROM accounting_core.tenant_account
              JOIN accounting_core.legal_entity_record
                ON legal_entity_record.tenant_account_id = tenant_account.tenant_account_id
              JOIN accounting_core.accounting_book
                ON accounting_book.tenant_account_id = tenant_account.tenant_account_id
               AND accounting_book.legal_entity_id = legal_entity_record.legal_entity_id
              JOIN accounting_core.accounting_book_period_control
                ON accounting_book_period_control.tenant_account_id = tenant_account.tenant_account_id
               AND accounting_book_period_control.accounting_book_id = accounting_book.accounting_book_id
              JOIN accounting_core.fiscal_period
                ON fiscal_period.tenant_account_id = accounting_book_period_control.tenant_account_id
               AND fiscal_period.fiscal_period_id = accounting_book_period_control.fiscal_period_id
             WHERE tenant_account.tenant_account_id = %s
               AND accounting_book.book_name = %s
            """,
            (self.case.tenant_id, self.case.policy.accounting_book_reference),
        ).fetchone()
        assert row is not None
        return row


if __name__ == "__main__":
    unittest.main()
