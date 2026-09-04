"""Real PostgreSQL regressions for financial-report source authority."""

from __future__ import annotations

import uuid
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REGISTRY_MIGRATION = (
    ROOT / "database/migrations/0020_financial_report_source_registry.sql"
)


class PostgresFinancialReportSourceRegistryTests(unittest.TestCase):
    """Prove source provenance controls against real PostgreSQL 18 semantics."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the existing foundation once before transaction-local migration tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one tenant, legal entity, book, and fiscal period using canonical fixtures."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_database_derives_book_period_authority_and_rejects_bad_sources(self) -> None:
        """Caller labels cannot override book status or admit cross-scope/future snapshots."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._apply_registry_inside_transaction(connection)
            tenant_id, legal_entity_id, book_id, period_id = self._accounting_scope(connection)

            connection.execute(
                """
                UPDATE accounting_core.accounting_book_period_control
                   SET period_status_code = 'hard_closed',
                       period_closed_at = clock_timestamp()
                 WHERE tenant_account_id = %s
                   AND accounting_book_id = %s
                   AND fiscal_period_id = %s
                """,
                (tenant_id, book_id, period_id),
            )

            current_snapshot_id = self._insert_snapshot(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
                currency_code="KRW",
                generated_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            )

            report_run_id = connection.execute(
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
                ) VALUES (%s, %s, %s, %s, 'USD', 'open', %s, 'management_review', 'superseded')
                RETURNING financial_report_run_id,
                          reporting_currency_code,
                          source_period_status_code,
                          knowledge_cutoff_at,
                          run_status_code
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    datetime(2000, 1, 1, tzinfo=timezone.utc),
                ),
            ).fetchone()
            assert report_run_id is not None
            run_id, currency_code, period_status_code, cutoff_at, run_status_code = report_run_id
            self.assertEqual(currency_code, "KRW")
            self.assertEqual(period_status_code, "hard_closed")
            self.assertGreater(cutoff_at, datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(run_status_code, "collecting_sources")

            source_id = connection.execute(
                """
                INSERT INTO accounting_reporting.financial_report_source (
                    tenant_account_id,
                    financial_report_run_id,
                    period_context_code,
                    trial_balance_snapshot_id,
                    source_role_code
                ) VALUES (%s, %s, 'current', %s, 'financial_statement_population')
                RETURNING financial_report_source_id
                """,
                (tenant_id, run_id, current_snapshot_id),
            ).fetchone()[0]
            self.assertIsNotNone(source_id)

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "financial_report_source_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_reporting.financial_report_source
                       SET source_role_code = 'financial_statement_population'
                     WHERE financial_report_source_id = %s
                    """,
                    (source_id,),
                )
            connection.rollback()

        # Recreate the transaction because PostgreSQL aborts the transaction after
        # the expected trigger exception above.
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._apply_registry_inside_transaction(connection)
            tenant_id, legal_entity_id, book_id, period_id = self._accounting_scope(connection)
            report_run_id = self._insert_report_run(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
            )
            other_book_id = self._insert_sibling_book(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                period_id=period_id,
            )
            other_snapshot_id = self._insert_snapshot(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=other_book_id,
                period_id=period_id,
                currency_code="KRW",
                generated_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "financial_report_source_scope_invalid",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_reporting.financial_report_source (
                        tenant_account_id,
                        financial_report_run_id,
                        period_context_code,
                        trial_balance_snapshot_id,
                        source_role_code
                    ) VALUES (%s, %s, 'current', %s, 'financial_statement_population')
                    """,
                    (tenant_id, report_run_id, other_snapshot_id),
                )
            connection.rollback()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._apply_registry_inside_transaction(connection)
            tenant_id, legal_entity_id, book_id, period_id = self._accounting_scope(connection)
            report_run_id = self._insert_report_run(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
            )
            future_snapshot_id = self._insert_snapshot(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
                currency_code="KRW",
                generated_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "financial_report_future_source",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_reporting.financial_report_source (
                        tenant_account_id,
                        financial_report_run_id,
                        period_context_code,
                        trial_balance_snapshot_id,
                        source_role_code
                    ) VALUES (%s, %s, 'current', %s, 'financial_statement_population')
                    """,
                    (tenant_id, report_run_id, future_snapshot_id),
                )
            connection.rollback()

    def test_registry_tables_force_rls_in_postgresql_catalog(self) -> None:
        """Both source-authority tables have PostgreSQL FORCE RLS enabled."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._apply_registry_inside_transaction(connection)
            for table_name in ("financial_report_run", "financial_report_source"):
                with self.subTest(table_name=table_name):
                    row = connection.execute(
                        """
                        SELECT pg_class.relrowsecurity, pg_class.relforcerowsecurity
                          FROM pg_class
                          JOIN pg_namespace
                            ON pg_namespace.oid = pg_class.relnamespace
                         WHERE pg_namespace.nspname = 'accounting_reporting'
                           AND pg_class.relname = %s
                        """,
                        (table_name,),
                    ).fetchone()
                    self.assertEqual(row, (True, True))
            connection.rollback()

    @staticmethod
    def _apply_registry_inside_transaction(connection: psycopg.Connection) -> None:
        """Execute migration 0020 without its outer transaction so test DDL can roll back."""
        migration = SOURCE_REGISTRY_MIGRATION.read_text(encoding="utf-8")
        statements = migration.removeprefix("BEGIN;\n").removesuffix("\nCOMMIT;\n")
        connection.execute(statements)

    def _accounting_scope(self, connection: psycopg.Connection) -> tuple[object, object, object, object]:
        """Return the one canonical accounting scope seeded for this test case."""
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

    @staticmethod
    def _insert_snapshot(
        connection: psycopg.Connection,
        *,
        tenant_id: object,
        legal_entity_id: object,
        book_id: object,
        period_id: object,
        currency_code: str,
        generated_at: datetime,
    ) -> object:
        """Insert retained trial-balance evidence for an explicit accounting scope."""
        return connection.execute(
            """
            INSERT INTO accounting_reporting.trial_balance_snapshot (
                tenant_account_id,
                legal_entity_id,
                accounting_book_id,
                fiscal_period_id,
                snapshot_currency_code,
                snapshot_generated_at,
                source_journal_count,
                source_payload_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
            RETURNING trial_balance_snapshot_id
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                period_id,
                currency_code,
                generated_at,
                "sha256:" + "1" * 64,
            ),
        ).fetchone()[0]

    @staticmethod
    def _insert_report_run(
        connection: psycopg.Connection,
        *,
        tenant_id: object,
        legal_entity_id: object,
        book_id: object,
        period_id: object,
    ) -> object:
        """Insert a source-collecting report run and return its database identity."""
        return connection.execute(
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
            ) VALUES (%s, %s, %s, %s, 'USD', 'hard_closed', %s, 'integration_test', 'superseded')
            RETURNING financial_report_run_id
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                period_id,
                datetime(2000, 1, 1, tzinfo=timezone.utc),
            ),
        ).fetchone()[0]

    @staticmethod
    def _insert_sibling_book(
        connection: psycopg.Connection,
        *,
        tenant_id: object,
        legal_entity_id: object,
        period_id: object,
    ) -> object:
        """Create a management book sharing the same calendar period for scope rejection."""
        suffix = uuid.uuid4().hex[:8]
        book_id = connection.execute(
            """
            INSERT INTO accounting_core.accounting_book (
                tenant_account_id,
                legal_entity_id,
                book_role_code,
                book_name,
                reporting_currency_code,
                valid_from
            ) VALUES (%s, %s, 'management', %s, 'KRW', %s)
            RETURNING accounting_book_id
            """,
            (
                tenant_id,
                legal_entity_id,
                f"urn:cwl:accounting_book:management_{suffix}",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO accounting_core.accounting_book_period_control (
                tenant_account_id,
                accounting_book_id,
                fiscal_period_id,
                period_status_code
            ) VALUES (%s, %s, %s, 'open')
            """,
            (tenant_id, book_id, period_id),
        )
        return book_id


if __name__ == "__main__":
    unittest.main()
