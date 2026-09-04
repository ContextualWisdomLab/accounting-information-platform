"""Real PostgreSQL RED/GREEN for immutable hard-close trial-balance evidence."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class TrialBalanceSnapshotImmutabilityPostgresTests(unittest.TestCase):
    """Keep a hard-close snapshot and its line population immutable after commit."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one posted population and close it through the production command."""
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
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-freeze:soft",
        )
        hard_close = self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="hard_closed",
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-freeze:hard",
        )
        self.assertEqual(hard_close.period_status_code, "hard_closed")

        with psycopg.connect(posting.DATABASE_URL) as connection:
            snapshot_row = connection.execute(
                """
                SELECT trial_balance_snapshot_id, accounting_book_id
                FROM accounting_reporting.trial_balance_snapshot
                WHERE tenant_account_id = %s
                ORDER BY snapshot_generated_at DESC, trial_balance_snapshot_id DESC
                LIMIT 1
                """,
                (self.case.tenant_id,),
            ).fetchone()
            self.assertIsNotNone(snapshot_row)
            assert snapshot_row is not None
            self.snapshot_id = snapshot_row[0]
            self.accounting_book_id = snapshot_row[1]
            line_row = connection.execute(
                """
                SELECT trial_balance_line_id
                FROM accounting_reporting.trial_balance_line
                WHERE tenant_account_id = %s
                  AND trial_balance_snapshot_id = %s
                ORDER BY trial_balance_line_id
                LIMIT 1
                """,
                (self.case.tenant_id, self.snapshot_id),
            ).fetchone()
            self.assertIsNotNone(line_row)
            assert line_row is not None
            self.line_id = line_row[0]

    def test_hard_close_snapshot_header_cannot_be_rewritten(self) -> None:
        """A committed close snapshot cannot later point at different source evidence."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_reporting.trial_balance_snapshot
                    SET source_payload_hash = %s
                    WHERE tenant_account_id = %s
                      AND trial_balance_snapshot_id = %s
                    """,
                    ("sha256:" + "9" * 64, self.case.tenant_id, self.snapshot_id),
                )
            connection.rollback()

    def test_hard_close_snapshot_header_cannot_be_deleted(self) -> None:
        """A committed close snapshot cannot be removed after it becomes evidence."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_immutable",
            ):
                connection.execute(
                    """
                    DELETE FROM accounting_reporting.trial_balance_snapshot
                    WHERE tenant_account_id = %s
                      AND trial_balance_snapshot_id = %s
                    """,
                    (self.case.tenant_id, self.snapshot_id),
                )
            connection.rollback()

    def test_hard_close_snapshot_line_cannot_be_rewritten(self) -> None:
        """A retained account balance cannot be changed after hard close."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_reporting.trial_balance_line
                    SET net_balance_amount = net_balance_amount + 1
                    WHERE tenant_account_id = %s
                      AND trial_balance_line_id = %s
                    """,
                    (self.case.tenant_id, self.line_id),
                )
            connection.rollback()

    def test_hard_close_snapshot_line_cannot_be_deleted(self) -> None:
        """A retained account balance cannot be removed after hard close."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_immutable",
            ):
                connection.execute(
                    """
                    DELETE FROM accounting_reporting.trial_balance_line
                    WHERE tenant_account_id = %s
                      AND trial_balance_line_id = %s
                    """,
                    (self.case.tenant_id, self.line_id),
                )
            connection.rollback()

    def test_hard_close_snapshot_population_cannot_be_extended(self) -> None:
        """A new valid chart account cannot be appended to an already closed snapshot."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            chart_account_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id,
                    accounting_book_id,
                    chart_account_code,
                    account_name,
                    normal_balance_code,
                    valid_from,
                    account_class_code
                )
                VALUES (%s, %s, %s, 'Post-close mutation probe', 'debit', %s, 'asset')
                RETURNING chart_account_id
                """,
                (
                    self.case.tenant_id,
                    self.accounting_book_id,
                    f"99{uuid.uuid4().hex[:10]}",
                    posting.VALID_FROM,
                ),
            ).fetchone()[0]
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_immutable",
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
                    )
                    VALUES (%s, %s, %s, 0, 0, 0)
                    """,
                    (self.case.tenant_id, self.snapshot_id, chart_account_id),
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
