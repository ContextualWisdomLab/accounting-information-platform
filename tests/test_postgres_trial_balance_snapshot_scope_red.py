"""Real PostgreSQL RED/GREEN for retained trial-balance evidence scope integrity."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class TrialBalanceSnapshotScopePostgresTests(unittest.TestCase):
    """Keep retained snapshot headers and lines inside one accounting-book scope."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Leave one balanced book-period soft-closed for controlled snapshot admission."""
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
            idempotency_key=f"{self.case.policy.tenant_reference}:snapshot-scope:soft",
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

    @staticmethod
    def _enable_period_closing_classification(connection: psycopg.Connection[object]) -> None:
        connection.execute(
            "SELECT set_config('accounting_core.journal_write_role', 'period_closing', true)"
        )

    def test_snapshot_header_rejects_legal_entity_from_another_book_scope(self) -> None:
        """A retained snapshot cannot pair one book with another legal entity."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            _, accounting_book_id, fiscal_period_id = self._scope(connection)
            other_legal_entity_id = connection.execute(
                """
                INSERT INTO accounting_core.legal_entity_record (
                    tenant_account_id,
                    legal_entity_code,
                    entity_name,
                    functional_currency_code,
                    valid_from
                )
                VALUES (%s, %s, 'Other scope entity', 'KRW', %s)
                RETURNING legal_entity_id
                """,
                (
                    self.case.tenant_id,
                    f"{self.case.policy.legal_entity_reference}:other",
                    posting.VALID_FROM,
                ),
            ).fetchone()[0]
            self._enable_period_closing_classification(connection)

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_book_entity_mismatch",
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
                        other_legal_entity_id,
                        accounting_book_id,
                        fiscal_period_id,
                        "sha256:" + "7" * 64,
                        f"{self.case.policy.tenant_reference}:snapshot-scope:wrong-entity",
                    ),
                )

    def test_snapshot_line_rejects_chart_account_from_another_book(self) -> None:
        """A retained line cannot import a chart account owned by another accounting book."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            legal_entity_id, accounting_book_id, fiscal_period_id = self._scope(connection)
            other_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                )
                VALUES (%s, %s, 'management', %s, 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    f"{self.case.policy.accounting_book_reference}:other",
                    posting.VALID_FROM,
                ),
            ).fetchone()[0]
            other_chart_account_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id,
                    accounting_book_id,
                    chart_account_code,
                    account_name,
                    normal_balance_code,
                    valid_from
                )
                VALUES (%s, %s, '990001', 'Other-book account', 'debit', %s)
                RETURNING chart_account_id
                """,
                (self.case.tenant_id, other_book_id, posting.VALID_FROM),
            ).fetchone()[0]
            self._enable_period_closing_classification(connection)
            snapshot_id = connection.execute(
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
                RETURNING trial_balance_snapshot_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    "sha256:" + "8" * 64,
                    f"{self.case.policy.tenant_reference}:snapshot-scope:line",
                ),
            ).fetchone()[0]

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_line_book_scope_mismatch",
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
                    VALUES (%s, %s, %s, 1, 0, 1)
                    """,
                    (self.case.tenant_id, snapshot_id, other_chart_account_id),
                )


if __name__ == "__main__":
    unittest.main()
