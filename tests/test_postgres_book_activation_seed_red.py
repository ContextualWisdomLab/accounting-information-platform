"""Real PostgreSQL regression for activating a book into an already-open period."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BookActivationSeedTests(unittest.TestCase):
    """Require activation to materialize the open book-period authority and fence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current migration chain into the PostgreSQL fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated tenant for the book-lifecycle regression."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_activating_book_seeds_existing_open_period_authority(self) -> None:
        """An inactive book becoming active must gain open control plus all 64 fences."""
        book_id = uuid.uuid4()
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
                SELECT fiscal_period_id
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND period_code = '2026-08'
                  AND period_status_code = 'open'
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
                    valid_from,
                    valid_to
                )
                VALUES (
                    %s, %s, %s, %s, %s, 'USD',
                    '2026-01-01T00:00:00Z',
                    '2026-07-31T23:59:59Z'
                )
                """,
                (
                    book_id,
                    self.case.tenant_id,
                    legal_entity_id,
                    f"activation_{book_id.hex}",
                    f"Activation {book_id.hex}",
                ),
            )
            connection.commit()

        self._assert_population(book_id, period_id, expected_control_count=0, expected_fences=0)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                """
                UPDATE accounting_core.accounting_book
                   SET valid_to = NULL
                 WHERE tenant_account_id = %s
                   AND accounting_book_id = %s
                """,
                (self.case.tenant_id, book_id),
            )
            connection.commit()

        self._assert_population(book_id, period_id, expected_control_count=1, expected_fences=64)

    def _assert_population(
        self,
        accounting_book_id: uuid.UUID,
        fiscal_period_id: uuid.UUID,
        *,
        expected_control_count: int,
        expected_fences: int,
    ) -> None:
        """Assert open authority is absent while inactive and complete after activation."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            control_rows = connection.execute(
                """
                SELECT period_status_code, period_closed_at
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            ).fetchall()
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

        self.assertEqual(len(control_rows), expected_control_count)
        self.assertEqual(fence_count, expected_fences)
        if expected_control_count:
            self.assertEqual(control_rows, [("open", None)])


if __name__ == "__main__":
    unittest.main()
