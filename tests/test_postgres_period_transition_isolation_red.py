"""Real PostgreSQL RED/GREEN for period-close transition isolation authority."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class PeriodTransitionIsolationPostgresTests(unittest.TestCase):
    """Reject close-state mutation that bypasses the supported snapshot-isolation contract."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete production migration chain used by posting tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one open book-period with its complete journal-population fence."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

    def _scope(self, connection: psycopg.Connection[object]) -> tuple[object, object]:
        row = connection.execute(
            """
            SELECT accounting_book.accounting_book_id,
                   accounting_book_period_control.fiscal_period_id
            FROM accounting_core.accounting_book
            JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = accounting_book.tenant_account_id
             AND accounting_book_period_control.accounting_book_id
                 = accounting_book.accounting_book_id
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id
                 = accounting_book_period_control.tenant_account_id
             AND fiscal_period.fiscal_period_id
                 = accounting_book_period_control.fiscal_period_id
            WHERE accounting_book.tenant_account_id = %s
              AND accounting_book.book_name = %s
              AND fiscal_period.period_code = '2026-08'
            """,
            (self.case.tenant_id, self.case.policy.accounting_book_reference),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row

    def _assert_transition_rejected(self, connection: psycopg.Connection[object]) -> None:
        accounting_book_id, fiscal_period_id = self._scope(connection)
        with self.assertRaisesRegex(
            psycopg.errors.CheckViolation,
            "period_close_isolation_required",
        ):
            connection.execute(
                """
                UPDATE accounting_core.accounting_book_period_control
                SET period_status_code = 'soft_closed',
                    period_closed_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            )
        connection.rollback()

    def test_read_committed_cannot_transition_book_period_close_state(self) -> None:
        """A raw READ COMMITTED writer cannot claim close authority from a weak snapshot."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            isolation = connection.execute(
                "SELECT current_setting('transaction_isolation')"
            ).fetchone()[0]
            self.assertEqual(isolation, "read committed")
            self._assert_transition_rejected(connection)

    def test_read_uncommitted_alias_cannot_transition_book_period_close_state(self) -> None:
        """PostgreSQL's weak READ UNCOMMITTED alias cannot bypass the close-isolation gate."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
            isolation = connection.execute(
                "SELECT current_setting('transaction_isolation')"
            ).fetchone()[0]
            self.assertEqual(isolation, "read uncommitted")
            self._assert_transition_rejected(connection)


if __name__ == "__main__":
    unittest.main()
