"""Real PostgreSQL regression for missing book-period close authority."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from accounting_information_platform import AccountingValidationError
from tests import test_postgres_posting as posting


class BookPeriodControlNoProjectionTests(unittest.TestCase):
    """Require close control to remain book-owned when a shared period is already closed."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current migration chain into the PostgreSQL fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated posting tenant for the authority-boundary regression."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_close_lock_does_not_synthesize_non_open_book_authority(self) -> None:
        """A missing book-period control must not be copied from shared fiscal-period state."""
        new_book_id = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s
                ORDER BY recorded_at
                LIMIT 1
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            period_id = connection.execute(
                """
                UPDATE accounting_core.fiscal_period
                   SET period_status_code = 'soft_closed',
                       period_closed_at = clock_timestamp()
                 WHERE tenant_account_id = %s
                   AND period_code = '2026-08'
                RETURNING fiscal_period_id
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    accounting_book_id,
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                )
                VALUES (%s, %s, %s, %s, %s, 'USD', '2026-01-01T00:00:00Z')
                """,
                (
                    new_book_id,
                    self.case.tenant_id,
                    legal_entity_id,
                    f"no_projection_{new_book_id.hex}",
                    f"No projection {new_book_id.hex}",
                ),
            )
            connection.commit()

        self._assert_no_control_or_fence(new_book_id, period_id)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "has no control row for this accounting book",
            ):
                self.case.ledger._lock_book_period(
                    connection,
                    self.case.tenant_id,
                    new_book_id,
                    "2026-08",
                )
            connection.rollback()

        self._assert_no_control_or_fence(new_book_id, period_id)

    def _assert_no_control_or_fence(
        self,
        accounting_book_id: uuid.UUID,
        fiscal_period_id: uuid.UUID,
    ) -> None:
        """Require the unsupported non-open pair to remain absent after close admission."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            control_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            ).fetchone()[0]
            fence_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.period_journal_population_fence
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            ).fetchone()[0]

        self.assertEqual(control_count, 0)
        self.assertEqual(fence_count, 0)


if __name__ == "__main__":
    unittest.main()
