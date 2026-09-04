"""Real PostgreSQL RED for journal admission racing a hard-close snapshot."""

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


class PeriodCloseJournalSerializationPostgresTests(unittest.TestCase):
    """Require hard-close evidence to include every journal admitted before close authority wins."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete production migration chain used by posting tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one posted book-period and move it to soft-close before the race."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
        self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="soft_closed",
            idempotency_key=f"{self.case.policy.tenant_reference}:close-race:soft",
        )

    def test_hard_close_cannot_freeze_a_snapshot_before_an_admitted_adjustment_commits(self) -> None:
        """A late soft-close adjustment is either in the snapshot or rejected before hard-close."""
        close_ledger = PostgresPostingLedger(
            posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        adjustment_ledger = PostgresPostingLedger(
            posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        pre_lock_snapshot_reached = threading.Event()
        adjustment_committed = threading.Event()
        close_result: list[object] = []
        close_errors: list[BaseException] = []
        original_acquire = close_ledger._acquire_command_lock

        def pause_before_period_lock(connection: object, lock_reference: str) -> None:
            if lock_reference.startswith("period:"):
                pre_lock_snapshot_reached.set()
                if not adjustment_committed.wait(timeout=10):
                    raise AssertionError("adjusting journal did not commit before close lock resumed")
            original_acquire(connection, lock_reference)

        def run_close() -> None:
            try:
                close_result.append(
                    close_ledger.close_fiscal_period(
                        self.case.policy.legal_entity_reference,
                        self.case.policy.accounting_book_reference,
                        "2026-08",
                        "KRW",
                        period_status_code="hard_closed",
                        idempotency_key=(
                            f"{self.case.policy.tenant_reference}:close-race:hard"
                        ),
                    )
                )
            except BaseException as error:  # noqa: BLE001 - thread transports exact failure
                close_errors.append(error)

        with mock.patch.object(
            close_ledger, "_acquire_command_lock", side_effect=pause_before_period_lock
        ):
            closer = threading.Thread(target=run_close, daemon=True)
            closer.start()
            self.assertTrue(
                pre_lock_snapshot_reached.wait(timeout=10),
                "hard-close did not reach the deterministic pre-lock snapshot boundary",
            )
            adjustment_ledger.post_adjusting_journal(
                legal_entity_reference=self.case.policy.legal_entity_reference,
                accounting_book_reference=self.case.policy.accounting_book_reference,
                period_code="2026-08",
                journal_date=date(2026, 8, 31),
                idempotency_key=f"{self.case.policy.tenant_reference}:close-race:adjusting",
                source_payload_hash="sha256:" + "a" * 64,
                proposal_id=str(uuid.uuid4()),
                transaction_currency="KRW",
                lines=(
                    PostedJournalLine(
                        line_number=1,
                        chart_account_code="110100",
                        account_role_code="accounts_receivable",
                        debit_amount=Decimal("7.250000"),
                        credit_amount=Decimal("0"),
                    ),
                    PostedJournalLine(
                        line_number=2,
                        chart_account_code="410100",
                        account_role_code="usage_revenue",
                        debit_amount=Decimal("0"),
                        credit_amount=Decimal("7.250000"),
                    ),
                ),
            )
            adjustment_committed.set()
            closer.join(timeout=20)

        self.assertFalse(closer.is_alive(), "hard-close remained blocked after adjustment commit")
        if close_errors:
            raise close_errors[0]
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
