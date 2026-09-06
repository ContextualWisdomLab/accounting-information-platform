"""Real PostgreSQL RED/GREEN for period close using immutable posted roles."""

from __future__ import annotations

import unittest
from decimal import Decimal

import psycopg

from tests import test_postgres_posting as posting


class PeriodClosePostedRoleStabilityPostgresTests(unittest.TestCase):
    """Keep hard-close classification bound to posted journal facts, not current catalog state."""

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

    def test_hard_close_uses_posted_role_after_catalog_mapping_expires(self) -> None:
        """A later catalog expiry cannot reclassify a journal that was already posted."""
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            updated = connection.execute(
                """
                UPDATE accounting_core.account_role_mapping
                   SET valid_to = TIMESTAMPTZ '2026-09-01 00:00:00+00'
                 WHERE tenant_account_id = %s
                   AND account_role_code = 'usage_revenue'
                   AND valid_to IS NULL
                """,
                (self.case.tenant_id,),
            ).rowcount
            posted_roles = connection.execute(
                """
                SELECT journal_entry_line.account_role_code,
                       journal_entry_line.credit_amount
                  FROM accounting_core.journal_entry_line
                  JOIN accounting_core.general_journal
                    ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                   AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                 WHERE general_journal.tenant_account_id = %s
                   AND journal_entry_line.account_role_code = 'usage_revenue'
                """,
                (self.case.tenant_id,),
            ).fetchall()

        self.assertEqual(updated, 1)
        self.assertEqual(posted_roles, [("usage_revenue", Decimal("25000.000000"))])

        receipt = self.case.ledger.close_fiscal_period(
            self.case.policy.legal_entity_reference,
            self.case.policy.accounting_book_reference,
            "2026-08",
            "KRW",
            period_status_code="hard_closed",
            idempotency_key=(
                f"{self.case.policy.tenant_reference}:posted-role-stability:hard-close"
            ),
        )

        with psycopg.connect(posting.DATABASE_URL) as connection:
            snapshot_lines = {
                str(row[0]): (Decimal(row[1]), Decimal(row[2]), Decimal(row[3]))
                for row in connection.execute(
                    """
                    SELECT chart_account.chart_account_code,
                           trial_balance_line.debit_total_amount,
                           trial_balance_line.credit_total_amount,
                           trial_balance_line.net_balance_amount
                      FROM accounting_reporting.trial_balance_snapshot
                      JOIN accounting_reporting.trial_balance_line
                        ON trial_balance_line.tenant_account_id = trial_balance_snapshot.tenant_account_id
                       AND trial_balance_line.trial_balance_snapshot_id = trial_balance_snapshot.trial_balance_snapshot_id
                      JOIN accounting_core.chart_account
                        ON chart_account.tenant_account_id = trial_balance_line.tenant_account_id
                       AND chart_account.chart_account_id = trial_balance_line.chart_account_id
                      JOIN accounting_core.fiscal_period
                        ON fiscal_period.tenant_account_id = trial_balance_snapshot.tenant_account_id
                       AND fiscal_period.fiscal_period_id = trial_balance_snapshot.fiscal_period_id
                     WHERE trial_balance_snapshot.tenant_account_id = %s
                       AND fiscal_period.period_code = '2026-08'
                    """,
                    (self.case.tenant_id,),
                ).fetchall()
            }

        self.assertEqual(receipt.period_status_code, "hard_closed")
        self.assertEqual(self.case._count_closing_journals(), 1)
        self.assertEqual(snapshot_lines["410100"], (Decimal("25000"), Decimal("25000"), Decimal("0")))
        self.assertEqual(snapshot_lines["310100"], (Decimal("0"), Decimal("25000"), Decimal("-25000")))


if __name__ == "__main__":
    unittest.main()
