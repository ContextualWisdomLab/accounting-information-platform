"""Real PostgreSQL RED/GREEN for trial-balance snapshot admission authority."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class TrialBalanceSnapshotAdmissionPostgresTests(unittest.TestCase):
    """Only the governed hard-close path may create a retained snapshot population."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Leave one balanced book-period soft-closed before hard-close authority."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
        self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="soft_closed",
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-admission:soft",
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

    def test_raw_soft_close_snapshot_insert_requires_close_authority(self) -> None:
        """A privileged SQL session cannot pre-populate retained close evidence directly."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            legal_entity_id, accounting_book_id, fiscal_period_id = self._scope(connection)
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_authority_required",
            ):
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
                        "sha256:" + "6" * 64,
                        f"{self.case.policy.tenant_reference}:snapshot-admission:forged",
                    ),
                )
            connection.rollback()

    def test_governed_hard_close_still_creates_one_snapshot(self) -> None:
        """The purpose-limited period-closing path remains the sole admitted writer."""
        receipt = self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="hard_closed",
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-admission:hard",
        )
        self.assertEqual(receipt.period_status_code, "hard_closed")
        with psycopg.connect(posting.DATABASE_URL) as connection:
            _, accounting_book_id, fiscal_period_id = self._scope(connection)
            count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_reporting.trial_balance_snapshot
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
