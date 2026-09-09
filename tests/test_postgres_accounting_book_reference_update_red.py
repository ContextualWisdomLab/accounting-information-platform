"""PostgreSQL RED for update-time Accounting Book reference overlap."""

from __future__ import annotations

from datetime import timedelta
import unittest

import psycopg

from tests import test_postgres_posting as posting


class PostgresAccountingBookReferenceUpdateRedTests(unittest.TestCase):
    """Require the durable-reference invariant on effective-time updates as well as inserts."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture for the update RED."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant and accounting catalog for each run."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_update_cannot_create_overlapping_book_reference_interval(self) -> None:
        """Closing or correcting effective dates must not introduce reference ambiguity."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.case.tenant_id),),
            )
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s
                  AND legal_entity_code = %s
                  AND valid_to IS NULL
                """,
                (
                    self.case.tenant_id,
                    self.case.policy.legal_entity_reference,
                ),
            ).fetchone()[0]

            reference = f"{self.case.policy.accounting_book_reference}-update-overlap"
            first_start = posting.VALID_FROM + timedelta(days=50)
            boundary = posting.VALID_FROM + timedelta(days=60)
            second_end = posting.VALID_FROM + timedelta(days=70)

            connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from,
                    valid_to
                ) VALUES (%s, %s, 'management', %s, 'KRW', %s, %s)
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    reference,
                    first_start,
                    boundary,
                ),
            )
            second_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from,
                    valid_to
                ) VALUES (%s, %s, 'statutory', %s, 'KRW', %s, %s)
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    reference,
                    boundary,
                    second_end,
                ),
            ).fetchone()[0]

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE accounting_core.accounting_book
                        SET valid_from = %s
                        WHERE tenant_account_id = %s
                          AND accounting_book_id = %s
                        """,
                        (
                            boundary - timedelta(days=1),
                            self.case.tenant_id,
                            second_book_id,
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
