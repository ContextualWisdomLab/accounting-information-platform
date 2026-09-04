"""Real PostgreSQL RED for financial-report source-population freezing."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import psycopg

from tests import test_postgres_posting as posting
from tests.test_postgres_financial_report_source_registry import (
    PostgresFinancialReportSourceRegistryTests,
)


class PostgresFinancialReportSourcePopulationFreezeTests(unittest.TestCase):
    """Prove a report-linked trial-balance population cannot change afterward."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the existing accounting foundation before rollback-only registry tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed the canonical tenant/entity/book/period fixture used by reporting tests."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_linked_snapshot_header_cannot_be_rewritten(self) -> None:
        """A retained report source must keep the exact snapshot header it admitted."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            run_id, snapshot_id = self._link_current_snapshot(connection)
            self.assertIsNotNone(run_id)

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "financial_report_source_population_frozen",
            ):
                connection.execute(
                    """
                    UPDATE accounting_reporting.trial_balance_snapshot
                       SET source_payload_hash = %s
                     WHERE trial_balance_snapshot_id = %s
                    """,
                    ("sha256:" + "2" * 64, snapshot_id),
                )
            connection.rollback()

    def test_linked_snapshot_cannot_gain_new_lines(self) -> None:
        """A source link must freeze the trial-balance line population atomically."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            _run_id, snapshot_id = self._link_current_snapshot(connection)
            tenant_id, _entity_id, book_id, _period_id = self._accounting_scope(connection)
            chart_account_id = connection.execute(
                """
                SELECT chart_account_id
                  FROM accounting_core.chart_account
                 WHERE tenant_account_id = %s
                   AND accounting_book_id = %s
                 ORDER BY chart_account_code, chart_account_id
                 LIMIT 1
                """,
                (tenant_id, book_id),
            ).fetchone()[0]

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "financial_report_source_population_frozen",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_reporting.trial_balance_line (
                        tenant_account_id,
                        trial_balance_snapshot_id,
                        chart_account_id,
                        debit_total_amount,
                        credit_total_amount,
                        net_balance_amount
                    ) VALUES (%s, %s, %s, 1.000000, 0.000000, 1.000000)
                    """,
                    (tenant_id, snapshot_id, chart_account_id),
                )
            connection.rollback()

    def _link_current_snapshot(
        self, connection: psycopg.Connection
    ) -> tuple[object, object]:
        """Install the registry and link one retained current-period snapshot."""
        PostgresFinancialReportSourceRegistryTests._apply_registry_inside_transaction(
            connection
        )
        tenant_id, legal_entity_id, book_id, period_id = self._accounting_scope(connection)
        snapshot_id = PostgresFinancialReportSourceRegistryTests._insert_snapshot(
            connection,
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            book_id=book_id,
            period_id=period_id,
            currency_code="KRW",
            generated_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        run_id = PostgresFinancialReportSourceRegistryTests._insert_report_run(
            connection,
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            book_id=book_id,
            period_id=period_id,
        )
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
            (tenant_id, run_id, snapshot_id),
        )
        return run_id, snapshot_id

    def _accounting_scope(
        self, connection: psycopg.Connection
    ) -> tuple[object, object, object, object]:
        """Resolve the canonical fixture scope without accepting caller authority."""
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
