"""Deterministic PostgreSQL RED for concurrent Accounting Book reference overlap."""

from __future__ import annotations

from datetime import timedelta
import threading
import time
import unittest

import psycopg

from tests import test_postgres_posting as posting


class PostgresAccountingBookReferenceConcurrencyOrderRedTests(unittest.TestCase):
    """Prove the competing write reaches PostgreSQL before the first writer is released."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture for the concurrency RED."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant and accounting catalog for each run."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_competing_overlap_reaches_postgres_before_first_commit(self) -> None:
        """A concurrent overlap must be database-visible before the first writer commits."""
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

        concurrent_reference = f"{self.case.policy.accounting_book_reference}-ordered-concurrency"
        first_start = posting.VALID_FROM + timedelta(days=30)
        first_end = posting.VALID_FROM + timedelta(days=40)
        second_start = posting.VALID_FROM + timedelta(days=35)
        second_end = posting.VALID_FROM + timedelta(days=45)
        second_started = threading.Event()
        second_insert_returned = threading.Event()
        allow_second_commit = threading.Event()
        second_outcome: dict[str, object] = {}

        with (
            psycopg.connect(posting.DATABASE_URL) as first_connection,
            psycopg.connect(posting.DATABASE_URL) as second_connection,
            psycopg.connect(posting.DATABASE_URL) as observer_connection,
        ):
            for connection in (first_connection, second_connection):
                connection.execute(
                    "SELECT set_config('app.tenant_account_id', %s, false)",
                    (str(self.case.tenant_id),),
                )
                connection.commit()

            second_backend_pid = second_connection.execute(
                "SELECT pg_backend_pid()"
            ).fetchone()[0]
            second_connection.commit()

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
                        second_insert_returned.set()
                        if not allow_second_commit.wait(timeout=10):
                            raise AssertionError("second writer commit gate was not released")
                except psycopg.IntegrityError as error:
                    second_outcome["integrity_error"] = error
                except Exception as error:  # pragma: no cover - surfaced explicitly below
                    second_outcome["unexpected_error"] = error
                else:
                    second_outcome["committed"] = True

            writer = threading.Thread(target=write_overlapping_book, daemon=True)
            writer.start()
            self.assertTrue(second_started.wait(timeout=5), "second writer did not start")

            competing_insert_reached_database = second_insert_returned.is_set()
            deadline = time.monotonic() + 5
            while not competing_insert_reached_database and time.monotonic() < deadline:
                activity = observer_connection.execute(
                    """
                    SELECT state, query
                    FROM pg_stat_activity
                    WHERE pid = %s
                    """,
                    (second_backend_pid,),
                ).fetchone()
                if (
                    activity is not None
                    and activity[0] == "active"
                    and "INSERT INTO accounting_core.accounting_book" in activity[1]
                ):
                    competing_insert_reached_database = True
                    break
                if second_insert_returned.wait(timeout=0.02):
                    competing_insert_reached_database = True
                    break

            if not competing_insert_reached_database:
                first_connection.rollback()
                allow_second_commit.set()
                second_connection.cancel()
                writer.join(timeout=5)
                self.fail(
                    "second writer did not reach the accounting_book INSERT before first-writer release"
                )

            first_connection.commit()
            allow_second_commit.set()
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
                "both concurrent writers committed after the competing INSERT reached PostgreSQL",
            )
            self.assertNotIn("committed", second_outcome)


if __name__ == "__main__":
    unittest.main()
