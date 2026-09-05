"""Real PostgreSQL regression for the period-close journal population fence."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class OpenPeriodJournalFencePostgresTests(unittest.TestCase):
    """Keep ordinary open-period posting off the close-control write hotspot."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete production migration chain used by posting tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant/book/period fixture in the open state."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _journal_population_revision(self) -> int:
        with psycopg.connect(posting.DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT accounting_book_period_control.journal_population_revision
                FROM accounting_core.accounting_book_period_control
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id
                     = accounting_book_period_control.tenant_account_id
                 AND accounting_book.accounting_book_id
                     = accounting_book_period_control.accounting_book_id
                JOIN accounting_core.fiscal_period
                  ON fiscal_period.tenant_account_id
                     = accounting_book_period_control.tenant_account_id
                 AND fiscal_period.fiscal_period_id
                     = accounting_book_period_control.fiscal_period_id
                WHERE accounting_book_period_control.tenant_account_id = %s
                  AND accounting_book.book_name = %s
                  AND fiscal_period.period_code = '2026-08'
                """,
                (
                    self.case.tenant_id,
                    self.case.policy.accounting_book_reference,
                ),
            ).fetchone()
        self.assertIsNotNone(row)
        return int(row[0])

    def test_open_period_posting_does_not_version_close_control_row(self) -> None:
        """Open-period journals may share the period fence but must not serialize on one row update."""
        before_revision = self._journal_population_revision()

        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

        self.assertEqual(self._journal_population_revision(), before_revision)


if __name__ == "__main__":
    unittest.main()
