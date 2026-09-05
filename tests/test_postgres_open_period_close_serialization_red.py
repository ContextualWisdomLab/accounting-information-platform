"""Real PostgreSQL regression for an open-period journal racing hard close."""

from __future__ import annotations

import threading
import unittest
import uuid
from datetime import date
from decimal import Decimal
from unittest import mock

import psycopg

from accounting_information_platform import PostedJournalLine, PostgresPostingLedger
from tests import test_postgres_posting as posting


class OpenPeriodCloseSerializationPostgresTests(unittest.TestCase):
    """Require direct open-to-hard-close evidence to include every admitted journal."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete production migration chain used by posting tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one open book-period with an initial authoritative journal."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

    def _scope(self, connection: object) -> tuple[object, object, object]:
        row = connection.execute(
            """
            SELECT legal_entity_record.legal_entity_id,
                   accounting_book.accounting_book_id,
                   fiscal_period.fiscal_period_id
            FROM accounting_core.legal_entity_record
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = legal_entity_record.tenant_account_id
             AND accounting_book.legal_entity_id = legal_entity_record.legal_entity_id
            JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id = accounting_book.tenant_account_id
             AND accounting_book_period_control.accounting_book_id = accounting_book.accounting_book_id
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id = accounting_book_period_control.tenant_account_id
             AND fiscal_period.fiscal_period_id = accounting_book_period_control.fiscal_period_id
            WHERE legal_entity_record.tenant_account_id = %s
              AND legal_entity_record.legal_entity_code = %s
              AND accounting_book.book_name = %s
              AND fiscal_period.period_code = '2026-08'
            """,
            (
                self.case.tenant_id,
                self.case.policy.legal_entity_reference,
                self.case.policy.accounting_book_reference,
            ),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row

    def test_open_period_role_context_without_close_lock_cannot_prepopulate_snapshot(self) -> None:
        """Closing capability plus a GUC is not enough to forge open-period retained evidence."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            legal_entity_id, accounting_book_id, fiscal_period_id = self._scope(connection)
            connection.execute(
                "SELECT set_config('accounting_core.journal_write_role', 'period_closing', true)"
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "trial_balance_snapshot_authority_required",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_reporting.trial_balance_snapshot (
                        tenant_account_id,
                        legal_entity_id,
                        accounting_book_id,
                        fiscal_period_id,
                        snapshot_currency_code,
                        source_journal_count,
                        source_payload_hash,
                        close_idempotency_key
                    )
                    VALUES (%s, %s, %s, %s, 'KRW', 1, %s, %s)
                    """,
                    (
                        self.case.tenant_id,
                        legal_entity_id,
                        accounting_book_id,
                        fiscal_period_id,
                        "sha256:" + "c" * 64,
                        f"{self.case.policy.tenant_reference}:open-close-race:forged",
                    ),
                )

    def test_incomplete_preseeded_fence_population_blocks_period_transition(self) -> None:
        """A missing stripe cannot degrade freshness validation into best-effort close."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            _legal_entity_id, accounting_book_id, fiscal_period_id = self._scope(connection)
            fence_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.period_journal_population_fence
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            ).fetchone()[0]
            self.assertEqual(fence_count, 64)
            connection.execute(
                """
                DELETE FROM accounting_core.period_journal_population_fence
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                  AND fence_slot = 63
                """,
                (self.case.tenant_id, accounting_book_id, fiscal_period_id),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "period_journal_population_fence_missing",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.accounting_book_period_control
                    SET period_status_code = 'soft_closed'
                    WHERE tenant_account_id = %s
                      AND accounting_book_id = %s
                      AND fiscal_period_id = %s
                    """,
                    (self.case.tenant_id, accounting_book_id, fiscal_period_id),
                )
            connection.rollback()

    def test_open_period_journal_committed_after_close_snapshot_invalidates_stale_close(self) -> None:
        """A stale direct hard close fails, then exact-key retry retains the admitted journal."""
        close_ledger = PostgresPostingLedger(
            posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        posting_ledger = PostgresPostingLedger(
            posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        close_idempotency_key = f"{self.case.policy.tenant_reference}:open-close-race:hard"
        pre_period_row_lock_reached = threading.Event()
        concurrent_post_committed = threading.Event()
        close_result: list[object] = []
        close_errors: list[BaseException] = []
        original_lock_book_period = close_ledger._lock_book_period

        def pause_before_period_row_lock(*args: object, **kwargs: object) -> object:
            pre_period_row_lock_reached.set()
            if not concurrent_post_committed.wait(timeout=10):
                raise AssertionError("open-period journal did not commit before close resumed")
            return original_lock_book_period(*args, **kwargs)

        def run_close() -> None:
            try:
                close_result.append(
                    close_ledger.close_fiscal_period(
                        self.case.policy.legal_entity_reference,
                        self.case.policy.accounting_book_reference,
                        "2026-08",
                        "KRW",
                        period_status_code="hard_closed",
                        idempotency_key=close_idempotency_key,
                    )
                )
            except BaseException as error:  # noqa: BLE001 - thread transports exact failure
                close_errors.append(error)

        with mock.patch.object(
            close_ledger, "_lock_book_period", side_effect=pause_before_period_row_lock
        ):
            closer = threading.Thread(target=run_close, daemon=True)
            closer.start()
            self.assertTrue(
                pre_period_row_lock_reached.wait(timeout=10),
                "hard-close did not reach the deterministic pre-period-row-lock boundary",
            )
            posting_ledger.post_adjusting_journal(
                legal_entity_reference=self.case.policy.legal_entity_reference,
                accounting_book_reference=self.case.policy.accounting_book_reference,
                period_code="2026-08",
                journal_date=date(2026, 8, 31),
                idempotency_key=f"{self.case.policy.tenant_reference}:open-close-race:journal",
                source_payload_hash="sha256:" + "b" * 64,
                proposal_id=str(uuid.uuid4()),
                transaction_currency="KRW",
                lines=(
                    PostedJournalLine(
                        line_number=1,
                        chart_account_code="110100",
                        account_role_code="accounts_receivable",
                        debit_amount=Decimal("11.750000"),
                        credit_amount=Decimal("0"),
                    ),
                    PostedJournalLine(
                        line_number=2,
                        chart_account_code="410100",
                        account_role_code="usage_revenue",
                        debit_amount=Decimal("0"),
                        credit_amount=Decimal("11.750000"),
                    ),
                ),
            )
            concurrent_post_committed.set()
            closer.join(timeout=20)

        self.assertFalse(closer.is_alive(), "hard-close remained blocked after open journal commit")
        self.assertEqual(close_result, [])
        self.assertEqual(len(close_errors), 1)
        self.assertIsInstance(close_errors[0], psycopg.errors.SerializationFailure)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            status_code, snapshot_count = connection.execute(
                """
                SELECT accounting_book_period_control.period_status_code,
                       (
                           SELECT COUNT(*)
                           FROM accounting_reporting.trial_balance_snapshot
                           WHERE trial_balance_snapshot.tenant_account_id = %s
                             AND trial_balance_snapshot.accounting_book_id
                                 = accounting_book_period_control.accounting_book_id
                             AND trial_balance_snapshot.fiscal_period_id
                                 = accounting_book_period_control.fiscal_period_id
                       )
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
                    self.case.tenant_id,
                    self.case.policy.accounting_book_reference,
                ),
            ).fetchone()
        self.assertEqual(status_code, "open")
        self.assertEqual(snapshot_count, 0)

        close_result.append(
            close_ledger.close_fiscal_period(
                self.case.policy.legal_entity_reference,
                self.case.policy.accounting_book_reference,
                "2026-08",
                "KRW",
                period_status_code="hard_closed",
                idempotency_key=close_idempotency_key,
            )
        )
        self.assertEqual(len(close_result), 1)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            live_debit, live_credit, retained_debit, retained_credit = connection.execute(
                """
                SELECT
                    COALESCE((
                        SELECT SUM(journal_entry_line.debit_amount)
                        FROM accounting_core.journal_entry_line
                        JOIN accounting_core.general_journal
                          ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                         AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                        JOIN accounting_core.chart_account
                          ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                         AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                        JOIN accounting_core.fiscal_period
                          ON fiscal_period.tenant_account_id = general_journal.tenant_account_id
                         AND fiscal_period.fiscal_period_id = general_journal.fiscal_period_id
                        WHERE general_journal.tenant_account_id = %s
                          AND chart_account.chart_account_code = '110100'
                          AND fiscal_period.period_code = '2026-08'
                    ), 0),
                    COALESCE((
                        SELECT SUM(journal_entry_line.credit_amount)
                        FROM accounting_core.journal_entry_line
                        JOIN accounting_core.general_journal
                          ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                         AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                        JOIN accounting_core.chart_account
                          ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                         AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                        JOIN accounting_core.fiscal_period
                          ON fiscal_period.tenant_account_id = general_journal.tenant_account_id
                         AND fiscal_period.fiscal_period_id = general_journal.fiscal_period_id
                        WHERE general_journal.tenant_account_id = %s
                          AND chart_account.chart_account_code = '110100'
                          AND fiscal_period.period_code = '2026-08'
                    ), 0),
                    trial_balance_line.debit_total_amount,
                    trial_balance_line.credit_total_amount
                FROM accounting_reporting.trial_balance_line
                JOIN accounting_reporting.trial_balance_snapshot
                  ON trial_balance_snapshot.tenant_account_id = trial_balance_line.tenant_account_id
                 AND trial_balance_snapshot.trial_balance_snapshot_id
                     = trial_balance_line.trial_balance_snapshot_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = trial_balance_line.tenant_account_id
                 AND chart_account.chart_account_id = trial_balance_line.chart_account_id
                JOIN accounting_core.fiscal_period
                  ON fiscal_period.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND fiscal_period.fiscal_period_id = trial_balance_snapshot.fiscal_period_id
                WHERE trial_balance_snapshot.tenant_account_id = %s
                  AND chart_account.chart_account_code = '110100'
                  AND fiscal_period.period_code = '2026-08'
                """,
                (self.case.tenant_id, self.case.tenant_id, self.case.tenant_id),
            ).fetchone()

        self.assertEqual(Decimal(live_debit), Decimal(retained_debit))
        self.assertEqual(Decimal(live_credit), Decimal(retained_credit))


if __name__ == "__main__":
    unittest.main()
