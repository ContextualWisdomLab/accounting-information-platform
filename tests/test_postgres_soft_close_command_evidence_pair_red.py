"""Real PostgreSQL RED/GREEN for the soft-close command-evidence commit pair."""

from __future__ import annotations

from pathlib import Path
import unittest

import psycopg

from tests import test_postgres_posting as posting


ROOT = Path(__file__).resolve().parents[1]
PAIR_MIGRATION = ROOT / "database/migrations/0037_soft_close_command_evidence_pair.sql"


class SoftCloseCommandEvidencePairPostgresTests(unittest.TestCase):
    """A soft-closed book period may commit only with its durable command evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one governed open book period with its complete population fence."""
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
        return row[0], row[1]

    def test_soft_close_cannot_commit_without_durable_command_evidence(self) -> None:
        """A raw database writer cannot retain soft-close authority without command evidence."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            accounting_book_id, fiscal_period_id = self._scope(connection)
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

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "soft_close_command_evidence_pair_required",
            ):
                connection.commit()
            connection.rollback()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            accounting_book_id, fiscal_period_id = self._scope(connection)
            row = connection.execute(
                """
                SELECT period_status_code,
                       soft_close_idempotency_key,
                       soft_close_source_payload_hash,
                       soft_close_source_journal_count
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            ).fetchone()

        self.assertEqual(row, ("open", None, None, None))

    def test_upgrade_refuses_preexisting_soft_close_without_command_evidence(self) -> None:
        """Migration 0037 may not grandfather a one-sided soft-close authority fact."""
        migration_sql = PAIR_MIGRATION.read_text(encoding="utf-8")

        with psycopg.connect(
            posting.DATABASE_URL,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            accounting_book_id, fiscal_period_id = self._scope(connection)
            connection.execute(
                """
                DROP TRIGGER soft_close_command_evidence_pair_guard
                ON accounting_core.accounting_book_period_control
                """
            )
            connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
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
            connection.execute("COMMIT")

        try:
            with psycopg.connect(
                posting.DATABASE_URL,
                autocommit=True,
                cursor_factory=psycopg.ClientCursor,
            ) as connection:
                with self.assertRaisesRegex(
                    psycopg.errors.CheckViolation,
                    "soft_close_command_evidence_pair_legacy_preflight",
                ):
                    connection.execute(migration_sql)
                connection.execute("ROLLBACK")
        finally:
            with psycopg.connect(
                posting.DATABASE_URL,
                autocommit=True,
                cursor_factory=psycopg.ClientCursor,
            ) as connection:
                connection.execute(
                    """
                    DROP TRIGGER IF EXISTS soft_close_command_evidence_pair_guard
                    ON accounting_core.accounting_book_period_control
                    """
                )
                connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
                connection.execute(
                    """
                    UPDATE accounting_core.accounting_book_period_control
                       SET period_status_code = 'open',
                           period_closed_at = NULL
                     WHERE tenant_account_id = %s
                       AND accounting_book_id = %s
                       AND fiscal_period_id = %s
                    """,
                    (self.case.tenant_id, accounting_book_id, fiscal_period_id),
                )
                connection.execute("COMMIT")
                connection.execute(migration_sql)


if __name__ == "__main__":
    unittest.main()
