"""Real PostgreSQL RED for durable effective-dated accounting-book references."""

from __future__ import annotations

from datetime import timedelta
import threading
import unittest

import psycopg

from tests import test_postgres_posting as posting


class PostgresAccountingBookReferenceIdentityRedTests(unittest.TestCase):
    """Prove one durable book reference names at most one Entity at any effective instant."""

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

    def test_finite_overlapping_history_is_rejected(self) -> None:
        """Two finite historical intervals for one reference must not overlap."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.case.tenant_id),),
            )
            legal_entity_id, current_valid_from = connection.execute(
                """
                SELECT book.legal_entity_id, book.valid_from
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
                    current_valid_from - timedelta(days=4),
                    current_valid_from - timedelta(days=2),
                ),
            )

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
                        ) VALUES (%s, %s, 'statutory', %s, 'KRW', %s, %s)
                        """,
                        (
                            self.case.tenant_id,
                            legal_entity_id,
                            self.case.policy.accounting_book_reference,
                            current_valid_from - timedelta(days=3),
                            current_valid_from - timedelta(days=1),
                        ),
                    )

    def test_concurrent_overlapping_book_reference_inserts_are_serialized(self) -> None:
        """Concurrent writers must not both commit overlapping durable-reference intervals."""
        with psycopg.connect(posting.DATABASE_URL) as lookup_connection:
            lookup_connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.case.tenant_id),),
            )
            legal_entity_id = lookup_connection.execute(
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

        concurrent_reference = f"{self.case.policy.accounting_book_reference}-concurrency"
        first_start = posting.VALID_FROM + timedelta(days=10)
        first_end = posting.VALID_FROM + timedelta(days=20)
        second_start = posting.VALID_FROM + timedelta(days=15)
        second_end = posting.VALID_FROM + timedelta(days=25)
        second_started = threading.Event()
        second_outcome: dict[str, object] = {}

        with (
            psycopg.connect(posting.DATABASE_URL) as first_connection,
            psycopg.connect(posting.DATABASE_URL) as second_connection,
        ):
            for connection in (first_connection, second_connection):
                connection.execute(
                    "SELECT set_config('app.tenant_account_id', %s, false)",
                    (str(self.case.tenant_id),),
                )
                connection.commit()

            first_connection.execute(
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
                    concurrent_reference,
                    first_start,
                    first_end,
                ),
            )

            def write_overlapping_book() -> None:
                try:
                    with second_connection.transaction():
                        second_started.set()
                        second_connection.execute(
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
                            """,
                            (
                                self.case.tenant_id,
                                legal_entity_id,
                                concurrent_reference,
                                second_start,
                                second_end,
                            ),
                        )
                except psycopg.IntegrityError as error:
                    second_outcome["integrity_error"] = error
                except Exception as error:  # pragma: no cover - surfaced explicitly below
                    second_outcome["unexpected_error"] = error
                else:
                    second_outcome["committed"] = True

            writer = threading.Thread(target=write_overlapping_book, daemon=True)
            writer.start()
            self.assertTrue(second_started.wait(timeout=5), "second writer did not start")
            first_connection.commit()
            writer.join(timeout=10)

            if writer.is_alive():
                second_connection.cancel()
                writer.join(timeout=5)
                self.fail("second writer did not reach a terminal database outcome")
            if "unexpected_error" in second_outcome:
                raise second_outcome["unexpected_error"]  # type: ignore[misc]

            self.assertIn(
                "integrity_error",
                second_outcome,
                "both concurrent writers committed overlapping durable-reference intervals",
            )
            self.assertNotIn("committed", second_outcome)

    def test_touching_book_reference_intervals_are_allowed(self) -> None:
        """A historical interval may end exactly when the current one begins."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.case.tenant_id),),
            )
            legal_entity_id, current_valid_from = connection.execute(
                """
                SELECT book.legal_entity_id, book.valid_from
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
                    current_valid_from - timedelta(days=1),
                    current_valid_from,
                ),
            ).fetchone()[0]

        self.assertIsNotNone(historical_id)

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
