"""Real PostgreSQL RED for hard close after chart-account catalog expiry."""

from __future__ import annotations

import unittest
from decimal import Decimal

import psycopg

from tests import test_postgres_posting as posting


class PeriodClosePostedAccountIdentityPostgresTests(unittest.TestCase):
    """Keep hard-close offsets bound to immutable posted chart-account identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current accounting migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one open book with the production posting catalog."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_hard_close_uses_posted_account_after_chart_account_expires(self) -> None:
        """A later chart-account expiry cannot strand or redirect an already-posted balance."""
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            source_account_id = connection.execute(
                """
                SELECT journal_entry_line.chart_account_id
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                WHERE general_journal.tenant_account_id = %s
                  AND chart_account.chart_account_code = '410100'
                  AND journal_entry_line.account_role_code = 'usage_revenue'
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            updated = connection.execute(
                """
                UPDATE accounting_core.chart_account
                   SET valid_to = TIMESTAMPTZ '2026-09-01 00:00:00+00'
                 WHERE tenant_account_id = %s
                   AND chart_account_id = %s
                   AND valid_to IS NULL
                """,
                (self.case.tenant_id, source_account_id),
            ).rowcount
            connection.commit()

        self.assertEqual(updated, 1)

        receipt = self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="hard_closed",
            idempotency_key=(
                f"{self.case.policy.tenant_reference}:posted-account-stability:hard-close"
            ),
        )

        with psycopg.connect(posting.DATABASE_URL) as connection:
            closing_account_id = connection.execute(
                """
                SELECT journal_entry_line.chart_account_id
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.journal_reference LIKE
                      'urn:cwl:accounting:general_journal:period_closing:%%'
                  AND journal_entry_line.account_role_code = 'usage_revenue'
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            snapshot_line = connection.execute(
                """
                SELECT trial_balance_line.debit_total_amount,
                       trial_balance_line.credit_total_amount,
                       trial_balance_line.net_balance_amount
                FROM accounting_reporting.trial_balance_snapshot
                JOIN accounting_reporting.trial_balance_line
                  ON trial_balance_line.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND trial_balance_line.trial_balance_snapshot_id = trial_balance_snapshot.trial_balance_snapshot_id
                WHERE trial_balance_snapshot.tenant_account_id = %s
                  AND trial_balance_line.chart_account_id = %s
                """,
                (self.case.tenant_id, source_account_id),
            ).fetchone()

        self.assertEqual(receipt.period_status_code, "hard_closed")
        self.assertEqual(closing_account_id, source_account_id)
        self.assertEqual(
            tuple(Decimal(value) for value in snapshot_line),
            (Decimal("25000"), Decimal("25000"), Decimal("0")),
        )

    def test_hard_close_does_not_redirect_posted_account_when_code_is_reused(self) -> None:
        """A successor account reusing the code cannot receive the historical closing contra."""
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
        transition_at = "2026-09-01 00:00:00+00"

        with psycopg.connect(posting.DATABASE_URL) as connection:
            source_account_id, book_id = connection.execute(
                """
                SELECT journal_entry_line.chart_account_id,
                       general_journal.accounting_book_id
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                WHERE general_journal.tenant_account_id = %s
                  AND chart_account.chart_account_code = '410100'
                  AND journal_entry_line.account_role_code = 'usage_revenue'
                """,
                (self.case.tenant_id,),
            ).fetchone()
            mapping_updated = connection.execute(
                """
                UPDATE accounting_core.account_role_mapping
                   SET valid_to = %s::timestamptz
                 WHERE tenant_account_id = %s
                   AND accounting_book_id = %s
                   AND chart_account_id = %s
                   AND account_role_code = 'usage_revenue'
                   AND valid_to IS NULL
                """,
                (transition_at, self.case.tenant_id, book_id, source_account_id),
            ).rowcount
            account_updated = connection.execute(
                """
                UPDATE accounting_core.chart_account
                   SET valid_to = %s::timestamptz
                 WHERE tenant_account_id = %s
                   AND chart_account_id = %s
                   AND valid_to IS NULL
                """,
                (transition_at, self.case.tenant_id, source_account_id),
            ).rowcount
            successor_account_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id,
                    accounting_book_id,
                    chart_account_code,
                    account_name,
                    normal_balance_code,
                    account_class_code,
                    valid_from
                )
                VALUES (%s, %s, '410100', 'Usage revenue successor', 'credit', 'revenue',
                        %s::timestamptz)
                RETURNING chart_account_id
                """,
                (self.case.tenant_id, book_id, transition_at),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.account_role_mapping (
                    tenant_account_id,
                    accounting_book_id,
                    account_role_code,
                    chart_account_id,
                    accounting_policy_version,
                    posting_rule_version,
                    valid_from
                )
                VALUES (%s, %s, 'usage_revenue', %s, %s, %s, %s::timestamptz)
                """,
                (
                    self.case.tenant_id,
                    book_id,
                    successor_account_id,
                    self.case.policy.accounting_policy_version,
                    self.case.policy.posting_rule_version,
                    transition_at,
                ),
            )
            connection.commit()

        self.assertEqual(mapping_updated, 1)
        self.assertEqual(account_updated, 1)
        self.assertNotEqual(successor_account_id, source_account_id)

        receipt = self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="hard_closed",
            idempotency_key=(
                f"{self.case.policy.tenant_reference}:posted-account-code-reuse:hard-close"
            ),
        )

        with psycopg.connect(posting.DATABASE_URL) as connection:
            closing_account_id = connection.execute(
                """
                SELECT journal_entry_line.chart_account_id
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.journal_reference LIKE
                      'urn:cwl:accounting:general_journal:period_closing:%%'
                  AND journal_entry_line.account_role_code = 'usage_revenue'
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            snapshot_line = connection.execute(
                """
                SELECT trial_balance_line.debit_total_amount,
                       trial_balance_line.credit_total_amount,
                       trial_balance_line.net_balance_amount
                FROM accounting_reporting.trial_balance_snapshot
                JOIN accounting_reporting.trial_balance_line
                  ON trial_balance_line.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND trial_balance_line.trial_balance_snapshot_id = trial_balance_snapshot.trial_balance_snapshot_id
                WHERE trial_balance_snapshot.tenant_account_id = %s
                  AND trial_balance_line.chart_account_id = %s
                """,
                (self.case.tenant_id, source_account_id),
            ).fetchone()

        self.assertEqual(receipt.period_status_code, "hard_closed")
        self.assertEqual(closing_account_id, source_account_id)
        self.assertNotEqual(closing_account_id, successor_account_id)
        self.assertEqual(
            tuple(Decimal(value) for value in snapshot_line),
            (Decimal("25000"), Decimal("25000"), Decimal("0")),
        )


if __name__ == "__main__":
    unittest.main()
