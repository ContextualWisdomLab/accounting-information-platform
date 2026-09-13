"""PostgreSQL RED for overlapping effective bank-account assignments."""

from __future__ import annotations

from datetime import timedelta
import threading
import time
import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BankAssignmentEffectiveOverlapRedTests(unittest.TestCase):
    """Require one bank-account/book binding at every accounting-effective instant."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture for temporal integrity."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create isolated tenant master data with the ordinary fixture cleanup."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_finite_assignments_for_same_bank_account_and_book_cannot_overlap(self) -> None:
        """Two finite bindings may not select different cash accounts at the same instant."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            fixture = self._insert_parent_fixture(connection, "finite-overlap")
            first_start = fixture["anchor"] - timedelta(days=4)
            first_end = fixture["anchor"] + timedelta(days=2)
            second_start = fixture["anchor"] - timedelta(days=1)
            second_end = fixture["anchor"] + timedelta(days=4)

            self.assertLess(first_start, second_start)
            self.assertLess(second_start, first_end)
            self.assertLess(first_end, second_end)
            self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["first_chart_account_id"],
                valid_from=first_start,
                valid_to=first_end,
                fixture_name="finite-overlap-first",
            )

            with self.assertRaises(psycopg.IntegrityError):
                self._insert_assignment(
                    connection,
                    fixture=fixture,
                    chart_account_id=fixture["second_chart_account_id"],
                    valid_from=second_start,
                    valid_to=second_end,
                    fixture_name="finite-overlap-second",
                )
            connection.rollback()

    def test_finite_assignment_cannot_overlap_existing_open_ended_assignment(self) -> None:
        """A finite row may not bypass the open-ended-only unique-index predicate."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            fixture = self._insert_parent_fixture(connection, "open-finite-overlap")
            open_start = fixture["anchor"] - timedelta(days=2)
            finite_start = fixture["anchor"] - timedelta(days=1)
            finite_end = fixture["anchor"] + timedelta(days=1)

            self.assertLess(open_start, finite_start)
            self.assertLess(finite_start, finite_end)
            self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["first_chart_account_id"],
                valid_from=open_start,
                valid_to=None,
                fixture_name="open-finite-overlap-open",
            )

            with self.assertRaises(psycopg.IntegrityError):
                self._insert_assignment(
                    connection,
                    fixture=fixture,
                    chart_account_id=fixture["second_chart_account_id"],
                    valid_from=finite_start,
                    valid_to=finite_end,
                    fixture_name="open-finite-overlap-finite",
                )
            connection.rollback()

    def test_adjacent_finite_assignments_for_same_bank_account_and_book_are_lawful(self) -> None:
        """Half-open history may hand off at one exact boundary without overlap."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            fixture = self._insert_parent_fixture(connection, "finite-adjacency")
            first_start = fixture["anchor"] - timedelta(days=4)
            shared_boundary = fixture["anchor"]
            second_end = fixture["anchor"] + timedelta(days=4)

            self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["first_chart_account_id"],
                valid_from=first_start,
                valid_to=shared_boundary,
                fixture_name="finite-adjacency-first",
            )
            self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["second_chart_account_id"],
                valid_from=shared_boundary,
                valid_to=second_end,
                fixture_name="finite-adjacency-second",
            )

            rows = connection.execute(
                """
                SELECT chart_account_id, valid_from, valid_to
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = %s
                  AND bank_account_record_id = %s
                  AND accounting_book_id = %s
                ORDER BY valid_from
                """,
                (
                    fixture["tenant_id"],
                    fixture["bank_account_id"],
                    fixture["book_id"],
                ),
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][2], rows[1][1])
            self.assertEqual(rows[0][0], fixture["first_chart_account_id"])
            self.assertEqual(rows[1][0], fixture["second_chart_account_id"])
            connection.rollback()

    def test_moving_successor_start_backward_cannot_create_assignment_overlap(self) -> None:
        """A lawful handoff may not become ambiguous by moving the successor start backward."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            fixture = self._insert_parent_fixture(connection, "update-successor-start")
            first_start = fixture["anchor"] - timedelta(days=4)
            shared_boundary = fixture["anchor"]
            second_end = fixture["anchor"] + timedelta(days=4)
            first_id = self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["first_chart_account_id"],
                valid_from=first_start,
                valid_to=shared_boundary,
                fixture_name="update-successor-start-first",
            )
            second_id = self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["second_chart_account_id"],
                valid_from=shared_boundary,
                valid_to=second_end,
                fixture_name="update-successor-start-second",
            )
            self.assertNotEqual(first_id, second_id)

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET valid_from = %s
                    WHERE tenant_account_id = %s
                      AND bank_account_assignment_id = %s
                    """,
                    (
                        shared_boundary - timedelta(days=1),
                        fixture["tenant_id"],
                        second_id,
                    ),
                )
            connection.rollback()

    def test_extending_predecessor_end_cannot_create_assignment_overlap(self) -> None:
        """A lawful handoff may not become ambiguous by extending the predecessor end."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            fixture = self._insert_parent_fixture(connection, "update-predecessor-end")
            first_start = fixture["anchor"] - timedelta(days=4)
            shared_boundary = fixture["anchor"]
            second_end = fixture["anchor"] + timedelta(days=4)
            first_id = self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["first_chart_account_id"],
                valid_from=first_start,
                valid_to=shared_boundary,
                fixture_name="update-predecessor-end-first",
            )
            second_id = self._insert_assignment(
                connection,
                fixture=fixture,
                chart_account_id=fixture["second_chart_account_id"],
                valid_from=shared_boundary,
                valid_to=second_end,
                fixture_name="update-predecessor-end-second",
            )
            self.assertNotEqual(first_id, second_id)

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET valid_to = %s
                    WHERE tenant_account_id = %s
                      AND bank_account_assignment_id = %s
                    """,
                    (
                        shared_boundary + timedelta(days=1),
                        fixture["tenant_id"],
                        first_id,
                    ),
                )
            connection.rollback()

    def test_concurrent_finite_overlap_reaches_postgres_before_first_commit(self) -> None:
        """Concurrent overlapping assignments must serialize at the database boundary."""
        with psycopg.connect(posting.DATABASE_URL) as setup_connection:
            fixture = self._insert_parent_fixture(setup_connection, "concurrent-overlap")
            setup_connection.commit()

        first_start = fixture["anchor"] - timedelta(days=4)
        first_end = fixture["anchor"] + timedelta(days=2)
        second_start = fixture["anchor"] - timedelta(days=1)
        second_end = fixture["anchor"] + timedelta(days=4)
        second_started = threading.Event()
        second_insert_returned = threading.Event()
        allow_second_commit = threading.Event()
        second_outcome: dict[str, object] = {}

        with (
            psycopg.connect(posting.DATABASE_URL) as first_connection,
            psycopg.connect(posting.DATABASE_URL) as second_connection,
            psycopg.connect(posting.DATABASE_URL, autocommit=True) as observer_connection,
        ):
            for connection in (first_connection, second_connection):
                connection.execute(
                    "SELECT set_config('app.tenant_account_id', %s, false)",
                    (str(fixture["tenant_id"]),),
                )
                connection.commit()

            second_backend_pid = second_connection.execute(
                "SELECT pg_backend_pid()"
            ).fetchone()[0]
            second_connection.commit()
            self._insert_assignment(
                first_connection,
                fixture=fixture,
                chart_account_id=fixture["first_chart_account_id"],
                valid_from=first_start,
                valid_to=first_end,
                fixture_name="concurrent-overlap-first",
            )

            def write_overlapping_assignment() -> None:
                try:
                    with second_connection.transaction():
                        second_started.set()
                        self._insert_assignment(
                            second_connection,
                            fixture=fixture,
                            chart_account_id=fixture["second_chart_account_id"],
                            valid_from=second_start,
                            valid_to=second_end,
                            fixture_name="concurrent-overlap-second",
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

            writer = threading.Thread(target=write_overlapping_assignment, daemon=True)
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
                    and "INSERT INTO accounting_core.bank_account_assignment" in activity[1]
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
                    "second writer did not reach the bank_account_assignment INSERT "
                    "before first-writer release"
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
                "both overlapping assignment writers committed after the competing INSERT "
                "reached PostgreSQL",
            )
            self.assertNotIn("committed", second_outcome)

    def _insert_parent_fixture(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        fixture_name: str,
    ) -> dict[str, object]:
        """Create one open-ended Book, two cash accounts, and one bank account."""
        tenant_id = connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.tenant_account
            WHERE tenant_account_code = %s
            """,
            (self.case.policy.tenant_reference,),
        ).fetchone()[0]
        connection.execute(
            "SELECT set_config('app.tenant_account_id', %s, false)",
            (str(tenant_id),),
        )
        legal_entity_id = connection.execute(
            """
            SELECT legal_entity_id
            FROM accounting_core.legal_entity_record
            WHERE tenant_account_id = %s
              AND legal_entity_code = %s
              AND valid_to IS NULL
            """,
            (tenant_id, self.case.policy.legal_entity_reference),
        ).fetchone()[0]
        anchor = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        parent_valid_from = anchor - timedelta(days=10)
        book_reference = f"urn:cwl:accounting_book:{fixture_name}:{uuid.uuid4().hex}"
        bank_account_reference = f"urn:cwl:bank_account:{fixture_name}:{uuid.uuid4().hex}"

        book_id = connection.execute(
            """
            INSERT INTO accounting_core.accounting_book (
                tenant_account_id,
                legal_entity_id,
                book_role_code,
                book_name,
                reporting_currency_code,
                valid_from,
                valid_to
            ) VALUES (%s, %s, 'management', %s, 'KRW', %s, NULL)
            RETURNING accounting_book_id
            """,
            (tenant_id, legal_entity_id, book_reference, parent_valid_from),
        ).fetchone()[0]
        first_chart_account_id = self._insert_chart_account(
            connection,
            tenant_id=tenant_id,
            book_id=book_id,
            valid_from=parent_valid_from,
            chart_account_code="199901",
            fixture_name=f"{fixture_name}-first",
        )
        second_chart_account_id = self._insert_chart_account(
            connection,
            tenant_id=tenant_id,
            book_id=book_id,
            valid_from=parent_valid_from,
            chart_account_code="199902",
            fixture_name=f"{fixture_name}-second",
        )
        bank_account_id = connection.execute(
            """
            INSERT INTO accounting_core.bank_account_record (
                tenant_account_id,
                bank_account_reference,
                account_currency_code,
                account_identifier_hash
            ) VALUES (%s, %s, 'KRW', %s)
            RETURNING bank_account_record_id
            """,
            (
                tenant_id,
                bank_account_reference,
                "sha256:" + uuid.uuid4().hex.ljust(64, "0"),
            ),
        ).fetchone()[0]
        return {
            "tenant_id": tenant_id,
            "legal_entity_id": legal_entity_id,
            "book_id": book_id,
            "bank_account_id": bank_account_id,
            "first_chart_account_id": first_chart_account_id,
            "second_chart_account_id": second_chart_account_id,
            "anchor": anchor,
        }

    @staticmethod
    def _insert_chart_account(
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        tenant_id: object,
        book_id: object,
        valid_from: object,
        chart_account_code: str,
        fixture_name: str,
    ) -> object:
        """Create one open-ended cash-account Entity that contains every test interval."""
        return connection.execute(
            """
            INSERT INTO accounting_core.chart_account (
                tenant_account_id,
                accounting_book_id,
                chart_account_code,
                account_name,
                normal_balance_code,
                valid_from,
                valid_to,
                account_class_code
            ) VALUES (%s, %s, %s, %s, 'debit', %s, NULL, 'asset')
            RETURNING chart_account_id
            """,
            (
                tenant_id,
                book_id,
                chart_account_code,
                f"Assignment overlap {fixture_name}",
                valid_from,
            ),
        ).fetchone()[0]

    @staticmethod
    def _insert_assignment(
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        fixture: dict[str, object],
        chart_account_id: object,
        valid_from: object,
        valid_to: object,
        fixture_name: str,
    ) -> object:
        """Insert one assignment with independent immutable command evidence."""
        return connection.execute(
            """
            INSERT INTO accounting_core.bank_account_assignment (
                tenant_account_id,
                bank_account_record_id,
                legal_entity_id,
                accounting_book_id,
                chart_account_id,
                valid_from,
                valid_to,
                assignment_idempotency_key,
                assignment_command_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING bank_account_assignment_id
            """,
            (
                fixture["tenant_id"],
                fixture["bank_account_id"],
                fixture["legal_entity_id"],
                fixture["book_id"],
                chart_account_id,
                valid_from,
                valid_to,
                f"{fixture_name}-{uuid.uuid4().hex}",
                "sha256:" + uuid.uuid4().hex.ljust(64, "0"),
            ),
        ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
