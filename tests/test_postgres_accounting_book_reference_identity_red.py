"""Real PostgreSQL RED for durable active accounting-book references."""

from __future__ import annotations

from datetime import timedelta
import unittest

import psycopg

from tests import test_postgres_posting as posting


class PostgresAccountingBookReferenceIdentityRedTests(unittest.TestCase):
    """Prove one externally durable book reference names at most one active Entity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture for the catalog RED."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create an isolated posting fixture and retain its cleanup semantics."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_duplicate_active_book_reference_is_rejected(self) -> None:
        """Two active Accounting Book Entities must not share one durable reference."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.case.tenant_id),),
            )
            legal_entity_id, existing_role = connection.execute(
                """
                SELECT book.legal_entity_id, book.book_role_code
                FROM accounting_core.accounting_book AS book
                JOIN accounting_core.legal_entity_record AS entity
                  ON entity.tenant_account_id = book.tenant_account_id
                 AND entity.legal_entity_id = book.legal_entity_id
                WHERE book.tenant_account_id = %s
                  AND entity.legal_entity_code = %s
                  AND book.book_name = %s
                  AND book.valid_to IS NULL
                """,
                (
                    self.case.tenant_id,
                    self.case.policy.legal_entity_reference,
                    self.case.policy.accounting_book_reference,
                ),
            ).fetchone()
            duplicate_role = "management" if existing_role != "management" else "statutory"

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO accounting_core.accounting_book (
                            tenant_account_id,
                            legal_entity_id,
                            book_role_code,
                            book_name,
                            reporting_currency_code,
                            valid_from
                        ) VALUES (%s, %s, %s, %s, 'KRW', %s)
                        """,
                        (
                            self.case.tenant_id,
                            legal_entity_id,
                            duplicate_role,
                            self.case.policy.accounting_book_reference,
                            posting.VALID_FROM + timedelta(seconds=1),
                        ),
                    )

    def test_overlapping_book_reference_validity_is_rejected(self) -> None:
        """Finite validity must not overlap another Entity carrying the same reference."""
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

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
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
                            self.case.policy.accounting_book_reference,
                            posting.VALID_FROM + timedelta(days=1),
                            posting.VALID_FROM + timedelta(days=2),
                        ),
                    )

    def test_expired_history_may_retain_the_same_book_reference(self) -> None:
        """Historical effective-time rows may reuse the reference when none is active."""
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
            historical_id = connection.execute(
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
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    self.case.policy.accounting_book_reference,
                    posting.VALID_FROM - timedelta(days=2),
                    posting.VALID_FROM - timedelta(days=1),
                ),
            ).fetchone()[0]

        self.assertIsNotNone(historical_id)


if __name__ == "__main__":
    unittest.main()
