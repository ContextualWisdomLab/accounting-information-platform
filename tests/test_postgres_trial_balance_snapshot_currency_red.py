"""Real PostgreSQL RED/GREEN for retained trial-balance currency scope."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class TrialBalanceSnapshotCurrencyPostgresTests(unittest.TestCase):
    """Bind retained hard-close currency to the authoritative accounting book."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Leave one balanced accounting book soft-closed for direct DB admission."""
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
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-currency:soft",
        )

    def test_snapshot_header_rejects_currency_different_from_accounting_book(self) -> None:
        """A retained snapshot cannot relabel the accounting book's reporting currency."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            scope = connection.execute(
                """
                SELECT legal_entity_record.legal_entity_id,
                       accounting_book.accounting_book_id,
                       fiscal_period.fiscal_period_id,
                       accounting_book.reporting_currency_code
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
            self.assertIsNotNone(scope)
            assert scope is not None
            legal_entity_id, accounting_book_id, fiscal_period_id, book_currency = scope
            wrong_currency = "USD" if book_currency != "USD" else "JPY"
            connection.execute(
                "SELECT set_config('accounting_core.journal_write_role', 'period_closing', true)"
            )

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_currency_mismatch",
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
                    VALUES (%s, %s, %s, %s, %s, 0, %s, %s)
                    """,
                    (
                        self.case.tenant_id,
                        legal_entity_id,
                        accounting_book_id,
                        fiscal_period_id,
                        wrong_currency,
                        "sha256:" + "c" * 64,
                        f"{self.case.policy.tenant_reference}:snapshot-currency:wrong",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
