"""Contracts for database-owned financial-report recording chronology."""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting
from tests.test_postgres_financial_report_source_registry import (
    PostgresFinancialReportSourceRegistryTests,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_financial_report_source_registry.sql"


class FinancialReportRecordingTimeContractTests(unittest.TestCase):
    """Require system recording time to be database-owned and immutable."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the provisional reporting-source migration."""
        cls.migration = MIGRATION.read_text(encoding="utf-8")

    def test_run_and_source_recording_times_are_database_owned(self) -> None:
        """Caller-supplied recorded_at values cannot become accounting evidence."""
        self.assertGreaterEqual(
            self.migration.count("NEW.recorded_at := clock_timestamp()"),
            2,
        )
        self.assertIn(
            "OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at",
            self.migration,
        )
        self.assertRegex(
            self.migration,
            re.compile(
                r"BEFORE UPDATE OF[\s\S]*?recorded_at[\s\S]*?"
                r"ON accounting_reporting\.financial_report_run",
                re.IGNORECASE,
            ),
        )


class PostgresFinancialReportRecordingTimeTests(unittest.TestCase):
    """Prove hostile caller chronology is overwritten by PostgreSQL 18."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the existing foundation before transaction-local registry tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one canonical tenant/accounting scope."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_report_run_recording_time_is_overwritten_and_immutable(self) -> None:
        """A report run cannot forge or later rewrite its system recording time."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            PostgresFinancialReportSourceRegistryTests._apply_registry_inside_transaction(
                connection
            )
            tenant_id, legal_entity_id, book_id, period_id = self._accounting_scope(
                connection
            )
            observed_before = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            hostile_recorded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
            run_id, recorded_at = connection.execute(
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
                    run_status_code,
                    recorded_at
                ) VALUES (
                    %s, %s, %s, %s,
                    'USD', 'open', %s, 'recording_time_test', 'superseded', %s
                )
                RETURNING financial_report_run_id, recorded_at
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    hostile_recorded_at,
                    hostile_recorded_at,
                ),
            ).fetchone()
            self.assertGreaterEqual(recorded_at, observed_before)

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "financial_report_scope_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_reporting.financial_report_run
                       SET recorded_at = %s
                     WHERE tenant_account_id = %s
                       AND financial_report_run_id = %s
                    """,
                    (hostile_recorded_at, tenant_id, run_id),
                )
            connection.rollback()

    def test_report_source_recording_time_is_overwritten(self) -> None:
        """A source link records database system time instead of caller chronology."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            PostgresFinancialReportSourceRegistryTests._apply_registry_inside_transaction(
                connection
            )
            tenant_id, legal_entity_id, book_id, period_id = self._accounting_scope(
                connection
            )
            snapshot_id = PostgresFinancialReportSourceRegistryTests._insert_snapshot(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
                currency_code="KRW",
                generated_at=datetime.now(timezone.utc),
            )
            run_id = PostgresFinancialReportSourceRegistryTests._insert_report_run(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
            )
            observed_before = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            hostile_recorded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
            recorded_at = connection.execute(
                """
                INSERT INTO accounting_reporting.financial_report_source (
                    tenant_account_id,
                    financial_report_run_id,
                    period_context_code,
                    trial_balance_snapshot_id,
                    source_role_code,
                    recorded_at
                ) VALUES (%s, %s, 'current', %s, 'financial_statement_population', %s)
                RETURNING recorded_at
                """,
                (tenant_id, run_id, snapshot_id, hostile_recorded_at),
            ).fetchone()[0]
            self.assertGreaterEqual(recorded_at, observed_before)
            connection.rollback()

    def _accounting_scope(
        self, connection: psycopg.Connection
    ) -> tuple[object, object, object, object]:
        """Return the seeded tenant, entity, book, and book-controlled period."""
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
