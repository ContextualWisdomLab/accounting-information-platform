"""Real PostgreSQL concurrency RED for one hard-close snapshot population per book-period."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class TrialBalanceSnapshotConcurrencyPostgresTests(unittest.TestCase):
    """Keep the one-population invariant valid across stale repeatable-read snapshots."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete production migration chain used by posting tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one posted book-period and leave it soft-closed without a snapshot."""
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
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-race:soft",
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            scope = connection.execute(
                """
                SELECT legal_entity_record.legal_entity_id,
                       accounting_book.accounting_book_id,
                       accounting_book_period_control.fiscal_period_id
                FROM accounting_core.legal_entity_record
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id = legal_entity_record.tenant_account_id
                 AND accounting_book.legal_entity_id = legal_entity_record.legal_entity_id
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
        self.assertIsNotNone(scope)
        assert scope is not None
        self.legal_entity_id, self.accounting_book_id, self.fiscal_period_id = scope

    def _insert_snapshot(
        self,
        connection: psycopg.Connection[object],
        *,
        generated_at: str,
        payload_digit: str,
        command_suffix: str,
    ) -> None:
        """Insert one raw pre-close population candidate for the exact authority scope."""
        connection.execute(
            """
            INSERT INTO accounting_reporting.trial_balance_snapshot (
                tenant_account_id,
                legal_entity_id,
                accounting_book_id,
                fiscal_period_id,
                snapshot_currency_code,
                snapshot_generated_at,
                source_journal_count,
                source_payload_hash,
                close_idempotency_key
            )
            VALUES (%s, %s, %s, %s, 'KRW', %s, 0, %s, %s)
            """,
            (
                self.case.tenant_id,
                self.legal_entity_id,
                self.accounting_book_id,
                self.fiscal_period_id,
                generated_at,
                "sha256:" + payload_digit * 64,
                f"{self.case.policy.tenant_reference}:snapshot-race:{command_suffix}",
            ),
        )

    def test_stale_repeatable_read_cannot_admit_second_snapshot_population(self) -> None:
        """A fixed pre-commit snapshot must not race around the one-population invariant."""
        stale = psycopg.connect(posting.DATABASE_URL)
        writer = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(stale.close)
        self.addCleanup(writer.close)
        try:
            stale.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            authority = stale.execute(
                """
                SELECT period_status_code
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (
                    self.case.tenant_id,
                    self.accounting_book_id,
                    self.fiscal_period_id,
                ),
            ).fetchone()
            self.assertEqual(authority, ("soft_closed",))

            self._insert_snapshot(
                writer,
                generated_at="2098-01-01T00:00:00Z",
                payload_digit="6",
                command_suffix="writer",
            )
            writer.commit()

            with self.assertRaises(psycopg.errors.UniqueViolation) as captured:
                self._insert_snapshot(
                    stale,
                    generated_at="2099-01-01T00:00:00Z",
                    payload_digit="7",
                    command_suffix="stale",
                )
            self.assertEqual(
                captured.exception.diag.constraint_name,
                "trial_balance_snapshot_one_population_per_book_period",
            )
        finally:
            stale.rollback()
            writer.rollback()


if __name__ == "__main__":
    unittest.main()
