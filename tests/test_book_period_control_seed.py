"""Real PostgreSQL regressions for book-period control and freshness-fence seeding."""

from __future__ import annotations

import threading
import unittest
import uuid
from datetime import date

import psycopg

from tests import test_postgres_posting as posting


class BookPeriodControlSeedTests(unittest.TestCase):
    """Require every active book-period pair to exist before journals can be admitted."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current migration chain into the PostgreSQL fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated tenant whose period and book are created after migration install."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_seeded_book_and_existing_period_have_control_and_all_fences(self) -> None:
        """Creating an active book after its period must materialize close authority immediately."""
        control_count, fence_count = self._control_and_fence_counts("2026-08")

        self.assertEqual(control_count, 1)
        self.assertEqual(fence_count, 64)

    def test_new_book_does_not_inherit_shared_closed_period_projection(self) -> None:
        """A new book must not receive close authority copied from the tenant-wide compatibility status."""
        new_book_id = uuid.uuid4()
        legal_entity_id, _fiscal_calendar_id = self._seed_scope_ids()

        with psycopg.connect(posting.DATABASE_URL) as connection:
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
            self._insert_book(
                connection,
                new_book_id=new_book_id,
                legal_entity_id=legal_entity_id,
            )
            connection.commit()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            control_row = connection.execute(
                """
                SELECT period_status_code, period_closed_at
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, new_book_id, period_id),
            ).fetchone()
            fence_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.period_journal_population_fence
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, new_book_id, period_id),
            ).fetchone()[0]

        self.assertIsNone(control_row)
        self.assertEqual(fence_count, 0)

    def test_period_open_seeds_control_and_all_fences_for_existing_book(self) -> None:
        """Opening a later period must materialize control and freshness rows for active books."""
        period_code = "2026-09"
        self.case.ledger.open_fiscal_period(
            self.case.policy.legal_entity_reference,
            period_code,
            date(2026, 9, 1),
            date(2026, 9, 30),
            idempotency_key=f"period-open:{uuid.uuid4()}",
            source_payload_hash="sha256:" + "7" * 64,
        )

        control_count, fence_count = self._control_and_fence_counts(period_code)

        self.assertEqual(control_count, 1)
        self.assertEqual(fence_count, 64)

    def test_concurrent_new_book_and_period_cannot_commit_without_pair(self) -> None:
        """Opposite-side master-data inserts must serialize before either trigger scans its peer."""
        new_book_id = uuid.uuid4()
        new_period_id = uuid.uuid4()
        period_code = f"seed-race-{uuid.uuid4().hex[:8]}"
        legal_entity_id, fiscal_calendar_id = self._seed_scope_ids()

        book_connection = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(book_connection.close)
        book_connection.execute("SET LOCAL lock_timeout = '5s'")
        self._insert_book(
            book_connection,
            new_book_id=new_book_id,
            legal_entity_id=legal_entity_id,
        )

        period_started = threading.Event()
        period_insert_completed = threading.Event()
        worker_errors: list[BaseException] = []

        def insert_period() -> None:
            try:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    connection.execute("SET LOCAL lock_timeout = '5s'")
                    period_started.set()
                    self._insert_period(
                        connection,
                        new_period_id=new_period_id,
                        fiscal_calendar_id=fiscal_calendar_id,
                        period_code=period_code,
                    )
                    connection.commit()
                period_insert_completed.set()
            except BaseException as error:  # pragma: no cover - surfaced on the main test thread
                worker_errors.append(error)
                period_insert_completed.set()

        worker = threading.Thread(target=insert_period, daemon=True)
        worker.start()
        self.assertTrue(period_started.wait(timeout=2.0))
        completed_before_book_commit = period_insert_completed.wait(timeout=1.0)

        book_connection.commit()
        worker.join(timeout=6.0)

        self.assertFalse(worker.is_alive(), "concurrent fiscal-period insert did not finish after peer commit")
        if worker_errors:
            raise worker_errors[0]
        self.assertFalse(
            completed_before_book_commit,
            "opposite-side seed trigger committed before the uncommitted active book became visible",
        )
        self._assert_control_and_fences(new_book_id, new_period_id)

    def test_repeatable_read_seed_race_fails_closed_then_retries_fresh(self) -> None:
        """A fixed snapshot must serialize-fail rather than commit a peer-blind master-data pair."""
        new_book_id = uuid.uuid4()
        new_period_id = uuid.uuid4()
        period_code = f"seed-rr-{uuid.uuid4().hex[:8]}"
        legal_entity_id, fiscal_calendar_id = self._seed_scope_ids()

        book_connection = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(book_connection.close)
        book_connection.execute("SET LOCAL lock_timeout = '5s'")
        self._insert_book(
            book_connection,
            new_book_id=new_book_id,
            legal_entity_id=legal_entity_id,
        )

        period_started = threading.Event()
        worker_errors: list[BaseException] = []

        def insert_repeatable_read_period() -> None:
            try:
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    connection.execute("SET LOCAL lock_timeout = '5s'")
                    period_started.set()
                    self._insert_period(
                        connection,
                        new_period_id=new_period_id,
                        fiscal_calendar_id=fiscal_calendar_id,
                        period_code=period_code,
                    )
                    connection.commit()
            except BaseException as error:  # pragma: no cover - asserted on the main test thread
                worker_errors.append(error)

        worker = threading.Thread(target=insert_repeatable_read_period, daemon=True)
        worker.start()
        self.assertTrue(period_started.wait(timeout=2.0))

        book_connection.commit()
        worker.join(timeout=6.0)

        self.assertFalse(worker.is_alive(), "repeatable-read fiscal-period insert did not terminate")
        self.assertEqual(len(worker_errors), 1)
        self.assertIsInstance(worker_errors[0], psycopg.errors.SerializationFailure)

        with psycopg.connect(posting.DATABASE_URL) as retry_connection:
            self._insert_period(
                retry_connection,
                new_period_id=new_period_id,
                fiscal_calendar_id=fiscal_calendar_id,
                period_code=period_code,
            )
            retry_connection.commit()

        self._assert_control_and_fences(new_book_id, new_period_id)

    def test_seed_sources_and_targets_finish_with_forced_rls(self) -> None:
        """Owner-only migration visibility must never leak into the committed runtime schema."""
        expected_relations = {
            "accounting_book",
            "fiscal_period",
            "accounting_book_period_control",
            "period_journal_population_fence",
        }
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT pg_class.relname,
                       pg_class.relrowsecurity,
                       pg_class.relforcerowsecurity
                FROM pg_catalog.pg_class
                JOIN pg_catalog.pg_namespace
                  ON pg_namespace.oid = pg_class.relnamespace
                WHERE pg_namespace.nspname = 'accounting_core'
                  AND pg_class.relname = ANY(%s)
                """,
                (list(expected_relations),),
            ).fetchall()

        actual = {
            str(name): (bool(rls_enabled), bool(rls_forced))
            for name, rls_enabled, rls_forced in rows
        }
        self.assertEqual(set(actual), expected_relations)
        self.assertEqual(actual, {name: (True, True) for name in expected_relations})

    def _seed_scope_ids(self) -> tuple[uuid.UUID, uuid.UUID]:
        """Return this fixture tenant's legal-entity and fiscal-calendar identifiers."""
        with psycopg.connect(posting.DATABASE_URL) as lookup:
            legal_entity_id = lookup.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s
                ORDER BY recorded_at
                LIMIT 1
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            fiscal_calendar_id = lookup.execute(
                """
                SELECT fiscal_calendar_id
                FROM accounting_core.fiscal_calendar
                WHERE tenant_account_id = %s
                ORDER BY created_at
                LIMIT 1
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
        return legal_entity_id, fiscal_calendar_id

    def _insert_book(
        self,
        connection: psycopg.Connection,
        *,
        new_book_id: uuid.UUID,
        legal_entity_id: uuid.UUID,
    ) -> None:
        """Insert one active book through the database master-data boundary."""
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
            VALUES (%s, %s, %s, %s, %s, 'USD', '2099-01-01T00:00:00Z')
            """,
            (
                new_book_id,
                self.case.tenant_id,
                legal_entity_id,
                f"seed_race_{new_book_id.hex}",
                f"Seed race {new_book_id.hex}",
            ),
        )

    def _insert_period(
        self,
        connection: psycopg.Connection,
        *,
        new_period_id: uuid.UUID,
        fiscal_calendar_id: uuid.UUID,
        period_code: str,
    ) -> None:
        """Insert one open period through the database master-data boundary."""
        connection.execute(
            """
            INSERT INTO accounting_core.fiscal_period (
                fiscal_period_id,
                tenant_account_id,
                fiscal_calendar_id,
                period_code,
                period_start_date,
                period_end_date,
                period_status_code
            )
            VALUES (%s, %s, %s, %s, DATE '2099-01-01', DATE '2099-01-31', 'open')
            """,
            (
                new_period_id,
                self.case.tenant_id,
                fiscal_calendar_id,
                period_code,
            ),
        )

    def _assert_control_and_fences(
        self,
        accounting_book_id: uuid.UUID,
        fiscal_period_id: uuid.UUID,
    ) -> None:
        """Require one control row and the complete 64-row freshness population."""
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

        self.assertEqual(control_count, 1)
        self.assertEqual(fence_count, 64)

    def _control_and_fence_counts(self, period_code: str) -> tuple[int, int]:
        """Return retained control and stripe cardinality for this fixture's primary book-period."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            book_id = connection.execute(
                """
                SELECT accounting_book_id
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s
                  AND book_name = %s
                  AND valid_to IS NULL
                """,
                (self.case.tenant_id, self.case.policy.accounting_book_reference),
            ).fetchone()[0]
            period_id = connection.execute(
                """
                SELECT fiscal_period_id
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND period_code = %s
                """,
                (self.case.tenant_id, period_code),
            ).fetchone()[0]
            control_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, book_id, period_id),
            ).fetchone()[0]
            fence_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.period_journal_population_fence
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, book_id, period_id),
            ).fetchone()[0]
        return int(control_count), int(fence_count)


if __name__ == "__main__":
    unittest.main()
