"""Real PostgreSQL RED/GREEN for commit-time hard-close snapshot pairing."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class TrialBalanceSnapshotCommitPairPostgresTests(unittest.TestCase):
    """A retained snapshot may commit only with the matching hard-closed book-period fact."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one governed book-period and leave it soft-closed."""
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
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-pair:soft",
        )

    def _scope(self, connection: psycopg.Connection[object]) -> tuple[object, object, object]:
        row = connection.execute(
            """
            SELECT legal_entity_record.legal_entity_id,
                   accounting_book.accounting_book_id,
                   fiscal_period.fiscal_period_id
            FROM accounting_core.legal_entity_record
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = legal_entity_record.tenant_account_id
             AND accounting_book.legal_entity_id = legal_entity_record.legal_entity_id
            JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id = accounting_book.tenant_account_id
             AND accounting_book_period_control.accounting_book_id = accounting_book.accounting_book_id
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id = accounting_book_period_control.tenant_account_id
             AND fiscal_period.fiscal_period_id = accounting_book_period_control.fiscal_period_id
            WHERE legal_entity_record.tenant_account_id = %s
              AND legal_entity_record.legal_entity_code = %s
              AND accounting_book.book_name = %s
              AND fiscal_period.period_code = '2026-08'
            """,
            (
                self.case.tenant_id,
                self.case.policy.legal_entity_reference,
                self.case.policy.accounting_book_reference,
            ),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row

    def test_soft_closed_snapshot_cannot_commit_without_hard_close_pair(self) -> None:
        """The closing capability cannot retain a snapshot while book authority stays soft-closed."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            legal_entity_id, accounting_book_id, fiscal_period_id = self._scope(connection)
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (
                    self.case.policy.tenant_reference,
                    f"period:{accounting_book_id}:2026-08",
                ),
            )
            connection.execute(
                """
                INSERT INTO accounting_reporting.trial_balance_snapshot (
                    tenant_account_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    snapshot_currency_code,
                    source_journal_count,
                    source_payload_hash,
                    close_idempotency_key
                )
                VALUES (%s, %s, %s, %s, 'KRW', 0, %s, %s)
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    "sha256:" + "7" * 64,
                    f"{self.case.policy.tenant_reference}:snapshot-pair:forged",
                ),
            )

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_hard_close_pair_required",
            ):
                connection.commit()
            connection.rollback()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            snapshot_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_reporting.trial_balance_snapshot
                WHERE tenant_account_id = %s
                  AND accounting_book_id = (
                      SELECT accounting_book_id
                      FROM accounting_core.accounting_book
                      WHERE tenant_account_id = %s
                        AND book_name = %s
                  )
                """,
                (
                    self.case.tenant_id,
                    self.case.tenant_id,
                    self.case.policy.accounting_book_reference,
                ),
            ).fetchone()[0]
            period_status = connection.execute(
                """
                SELECT accounting_book_period_control.period_status_code
                FROM accounting_core.accounting_book_period_control
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id = accounting_book_period_control.tenant_account_id
                 AND accounting_book.accounting_book_id = accounting_book_period_control.accounting_book_id
                JOIN accounting_core.fiscal_period
                  ON fiscal_period.tenant_account_id = accounting_book_period_control.tenant_account_id
                 AND fiscal_period.fiscal_period_id = accounting_book_period_control.fiscal_period_id
                WHERE accounting_book_period_control.tenant_account_id = %s
                  AND accounting_book.book_name = %s
                  AND fiscal_period.period_code = '2026-08'
                """,
                (self.case.tenant_id, self.case.policy.accounting_book_reference),
            ).fetchone()[0]

        self.assertEqual(snapshot_count, 0)
        self.assertEqual(period_status, "soft_closed")


if __name__ == "__main__":
    unittest.main()
