"""Real PostgreSQL regression for explicit rejection of direct close-authority inserts."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BookPeriodControlInsertAuthorityTests(unittest.TestCase):
    """Keep book-period close authority on the canonical database-owned seeding path."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current migration chain into the PostgreSQL fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated tenant and its normal open accounting book/period."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_direct_open_control_insert_fails_with_explicit_authority_error(self) -> None:
        """A raw INSERT must raise instead of silently dropping an unauthorized authority write."""
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
                    f"direct_control_{new_book_id.hex}",
                    f"Direct control {new_book_id.hex}",
                ),
            )
            connection.commit()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "book_period_control_insert_authority_required",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.accounting_book_period_control (
                        tenant_account_id,
                        accounting_book_id,
                        fiscal_period_id,
                        period_status_code,
                        period_closed_at
                    )
                    VALUES (%s, %s, %s, 'open', NULL)
                    """,
                    (self.case.tenant_id, new_book_id, period_id),
                )
            connection.rollback()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            control_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, new_book_id, period_id),
            ).fetchone()[0]

        self.assertEqual(control_count, 0)


if __name__ == "__main__":
    unittest.main()
