"""Real PostgreSQL regression for the period-close journal population fence."""

from __future__ import annotations

import threading
import unittest
import uuid
from unittest import mock

import psycopg

from accounting_information_platform import PostgresPostingLedger
from tests import test_postgres_posting as posting


class OpenPeriodJournalFencePostgresTests(unittest.TestCase):
    """Keep ordinary open-period posting off close-control serialization points."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete production migration chain used by posting tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant/book/period fixture in the open state."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _journal_population_revision(self) -> int:
        with psycopg.connect(posting.DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT accounting_book_period_control.journal_population_revision
                FROM accounting_core.accounting_book_period_control
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id
                     = accounting_book_period_control.tenant_account_id
                 AND accounting_book.accounting_book_id
                     = accounting_book_period_control.accounting_book_id
                JOIN accounting_core.fiscal_period
                  ON fiscal_period.tenant_account_id
                     = accounting_book_period_control.tenant_account_id
                 AND fiscal_period.fiscal_period_id
                     = accounting_book_period_control.fiscal_period_id
                WHERE accounting_book_period_control.tenant_account_id = %s
                  AND accounting_book.book_name = %s
                  AND fiscal_period.period_code = '2026-08'
                """,
                (
                    self.case.tenant_id,
                    self.case.policy.accounting_book_reference,
                ),
            ).fetchone()
        self.assertIsNotNone(row)
        return int(row[0])

    def _hold_book_period_share_lock(self, connection: object) -> None:
        row = connection.execute(
            """
            SELECT accounting_book_period_control.period_status_code
            FROM accounting_core.accounting_book_period_control
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id
                 = accounting_book_period_control.tenant_account_id
             AND accounting_book.accounting_book_id
                 = accounting_book_period_control.accounting_book_id
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id
                 = accounting_book_period_control.tenant_account_id
             AND fiscal_period.fiscal_period_id
                 = accounting_book_period_control.fiscal_period_id
            WHERE accounting_book_period_control.tenant_account_id = %s
              AND accounting_book.book_name = %s
              AND fiscal_period.period_code = '2026-08'
            FOR SHARE OF accounting_book_period_control
            """,
            (
                self.case.tenant_id,
                self.case.policy.accounting_book_reference,
            ),
        ).fetchone()
        self.assertEqual(row[0], "open")

    def test_open_period_posting_does_not_version_close_control_row(self) -> None:
        """Open-period journals may share the period fence but must not serialize on one row update."""
        before_revision = self._journal_population_revision()

        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

        self.assertEqual(self._journal_population_revision(), before_revision)

    def test_open_period_posting_can_progress_while_peer_holds_share_fence(self) -> None:
        """A peer shared fence must not turn ordinary journal admission into an exclusive-row queue."""
        posting_finished = threading.Event()
        posting_errors: list[BaseException] = []

        def post_journal() -> None:
            try:
                self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
            except BaseException as error:  # noqa: BLE001 - thread transports exact failure
                posting_errors.append(error)
            finally:
                posting_finished.set()

        with psycopg.connect(posting.DATABASE_URL) as fence_connection:
            self._hold_book_period_share_lock(fence_connection)
            worker = threading.Thread(target=post_journal, daemon=True)
            worker.start()
            progressed_while_share_lock_held = posting_finished.wait(timeout=5)
            fence_connection.rollback()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "ordinary posting remained blocked after fence release")
        self.assertEqual(posting_errors, [])
        self.assertTrue(
            progressed_while_share_lock_held,
            "ordinary open-period posting blocked behind a peer shared book-period fence",
        )
        self.assertEqual(self._journal_population_revision(), 0)

    def test_open_period_postings_do_not_serialize_on_application_period_lock(self) -> None:
        """Two ordinary posts may overlap before either journal row is written."""
        first_ledger = PostgresPostingLedger(
            posting.DATABASE_URL, tenant_reference=self.case.policy.tenant_reference
        )
        second_ledger = PostgresPostingLedger(
            posting.DATABASE_URL, tenant_reference=self.case.policy.tenant_reference
        )
        first_period_admission_reached = threading.Event()
        release_first_post = threading.Event()
        second_post_finished = threading.Event()
        first_errors: list[BaseException] = []
        second_errors: list[BaseException] = []
        original_require_open_period = first_ledger._require_open_book_period_bounds

        first_proposal = self.case._two_line_proposal(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=f"{self.case.policy.tenant_reference}:open-concurrency:first",
            source_payload_hash="sha256:" + "d" * 64,
            source_event_references=(
                f"{self.case.policy.tenant_reference}:open-concurrency:first",
            ),
        )
        second_proposal = self.case._two_line_proposal(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=f"{self.case.policy.tenant_reference}:open-concurrency:second",
            source_payload_hash="sha256:" + "e" * 64,
            source_event_references=(
                f"{self.case.policy.tenant_reference}:open-concurrency:second",
            ),
        )

        def pause_first_after_period_admission(*args: object, **kwargs: object) -> object:
            result = original_require_open_period(*args, **kwargs)
            first_period_admission_reached.set()
            if not release_first_post.wait(timeout=10):
                raise AssertionError("first open posting was not released after concurrency probe")
            return result

        def post_first() -> None:
            try:
                first_ledger.post(first_proposal, self.case.policy)
            except BaseException as error:  # noqa: BLE001 - thread transports exact failure
                first_errors.append(error)

        def post_second() -> None:
            try:
                second_ledger.post(second_proposal, self.case.policy)
            except BaseException as error:  # noqa: BLE001 - thread transports exact failure
                second_errors.append(error)
            finally:
                second_post_finished.set()

        with mock.patch.object(
            first_ledger,
            "_require_open_book_period_bounds",
            side_effect=pause_first_after_period_admission,
        ):
            first_worker = threading.Thread(target=post_first, daemon=True)
            first_worker.start()
            self.assertTrue(
                first_period_admission_reached.wait(timeout=10),
                "first posting did not reach the deterministic pre-journal boundary",
            )
            second_worker = threading.Thread(target=post_second, daemon=True)
            second_worker.start()
            second_progressed_while_first_was_paused = second_post_finished.wait(timeout=5)
            release_first_post.set()
            first_worker.join(timeout=10)
            second_worker.join(timeout=10)

        self.assertFalse(first_worker.is_alive(), "first posting did not finish after release")
        self.assertFalse(second_worker.is_alive(), "second posting did not finish after first release")
        self.assertEqual(first_errors, [])
        self.assertEqual(second_errors, [])
        self.assertTrue(
            second_progressed_while_first_was_paused,
            "ordinary open-period postings are serialized by the application period advisory lock",
        )


if __name__ == "__main__":
    unittest.main()
