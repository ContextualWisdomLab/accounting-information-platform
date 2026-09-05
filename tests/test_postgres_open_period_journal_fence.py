"""Real PostgreSQL regression for the period-close journal population fence."""

from __future__ import annotations

import threading
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

    def _hold_book_period_share_lock(self, connection: object) -> None:
        row = connection.execute(
            """
            SELECT accounting_book_period_control.period_status_code
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
            FOR SHARE OF accounting_book_period_control
            """,
            (
                self.case.tenant_id,
                self.case.policy.accounting_book_reference,
            ),
        ).fetchone()
        self.assertEqual(row[0], "open")

    def test_open_period_posting_does_not_version_close_control_row(self) -> None:
        """Open-period journals may share the period fence but must not serialize on one row update."""
        before_revision = self._journal_population_revision()

        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

        self.assertEqual(self._journal_population_revision(), before_revision)

    def test_open_period_posting_can_progress_while_peer_holds_share_fence(self) -> None:
        """A peer shared fence must not turn ordinary journal admission into an exclusive-row queue."""
        posting_finished = threading.Event()
        posting_errors: list[BaseException] = []

        def post_journal() -> None:
            try:
                self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
            except BaseException as error:  # noqa: BLE001 - thread transports exact failure
                posting_errors.append(error)
            finally:
                posting_finished.set()

        with psycopg.connect(posting.DATABASE_URL) as fence_connection:
            self._hold_book_period_share_lock(fence_connection)
            worker = threading.Thread(target=post_journal, daemon=True)
            worker.start()
            progressed_while_share_lock_held = posting_finished.wait(timeout=5)
            fence_connection.rollback()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "ordinary posting remained blocked after fence release")
        self.assertEqual(posting_errors, [])
        self.assertTrue(
            progressed_while_share_lock_held,
            "ordinary open-period posting blocked behind a peer shared book-period fence",
        )
        self.assertEqual(self._journal_population_revision(), 0)


if __name__ == "__main__":
    unittest.main()
