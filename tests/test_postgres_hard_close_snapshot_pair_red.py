"""Real PostgreSQL RED/GREEN for the hard-close-to-snapshot commit pair."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class HardCloseSnapshotPairPostgresTests(unittest.TestCase):
    """A hard-closed book period may commit only with its retained trial balance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one governed book period and leave it soft-closed without a snapshot."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="soft_closed",
            idempotency_key=f"{self.case.policy.tenant_reference}:hard-close-pair:soft",
        )

    def _scope(self, connection: psycopg.Connection[object]) -> tuple[object, object]:
        row = connection.execute(
            """
            SELECT accounting_book.accounting_book_id,
                   fiscal_period.fiscal_period_id
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
        return row[0], row[1]

    def test_hard_close_cannot_commit_without_matching_snapshot(self) -> None:
        """A database writer cannot retain hard-closed authority without retained evidence."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            accounting_book_id, fiscal_period_id = self._scope(connection)
            connection.execute(
                """
                UPDATE accounting_core.accounting_book_period_control
                   SET period_status_code = 'hard_closed',
                       period_closed_at = clock_timestamp()
                 WHERE tenant_account_id = %s
                   AND accounting_book_id = %s
                   AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            )

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "hard_close_snapshot_pair_required",
            ):
                connection.commit()
            connection.rollback()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            accounting_book_id, fiscal_period_id = self._scope(connection)
            period_status, snapshot_count = connection.execute(
                """
                SELECT accounting_book_period_control.period_status_code,
                       (
                           SELECT count(*)
                           FROM accounting_reporting.trial_balance_snapshot
                           WHERE trial_balance_snapshot.tenant_account_id
                                 = accounting_book_period_control.tenant_account_id
                             AND trial_balance_snapshot.accounting_book_id
                                 = accounting_book_period_control.accounting_book_id
                             AND trial_balance_snapshot.fiscal_period_id
                                 = accounting_book_period_control.fiscal_period_id
                       )
                FROM accounting_core.accounting_book_period_control
                WHERE accounting_book_period_control.tenant_account_id = %s
                  AND accounting_book_period_control.accounting_book_id = %s
                  AND accounting_book_period_control.fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            ).fetchone()

        self.assertEqual(period_status, "soft_closed")
        self.assertEqual(snapshot_count, 0)


if __name__ == "__main__":
    unittest.main()
