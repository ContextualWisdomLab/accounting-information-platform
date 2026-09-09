"""PostgreSQL RED for bank-assignment chart-account effective-time admission."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    accept_bank_account_assignment,
    accept_bank_account_record,
)
from tests import test_postgres_posting as posting


class BankAssignmentChartAccountEffectiveTimeRedTests(unittest.TestCase):
    """Require bank-account assignments to bind a chart account effective at assignment start."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_assignment_rejects_chart_account_not_effective_at_assignment_valid_from(self) -> None:
        """A current chart account cannot receive a binding that predates its interval."""
        bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"fixture-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

        with psycopg.connect(posting.DATABASE_URL) as connection:
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
            book_id = connection.execute(
                """
                UPDATE accounting_core.accounting_book
                SET valid_from = TIMESTAMPTZ '2026-08-01 00:00:00+00'
                WHERE tenant_account_id = %s
                  AND book_name = %s
                RETURNING accounting_book_id
                """,
                (tenant_id, self.case.policy.accounting_book_reference),
            ).fetchone()[0]
            chart_valid_from, database_now = connection.execute(
                """
                UPDATE accounting_core.chart_account
                SET valid_from = TIMESTAMPTZ '2026-09-01 00:00:00+00',
                    valid_to = NULL
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND chart_account_code = %s
                RETURNING valid_from, clock_timestamp()
                """,
                (tenant_id, book_id, "110200"),
            ).fetchone()
        self.assertGreater(database_now, chart_valid_from)

        with self.assertRaises(AccountingValidationError):
            accept_bank_account_assignment(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": bank_account_reference,
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "accounting_book_reference": self.case.policy.accounting_book_reference,
                    "chart_account_code": "110200",
                    "valid_from": "2026-08-31T00:00:00Z",
                    "assignment_idempotency_key": f"assign-chart-effective-time-{uuid.uuid4().hex}",
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(tenant_id),),
            )
            assignment_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.bank_account_assignment AS assignment
                JOIN accounting_core.bank_account_record AS account
                  ON account.tenant_account_id = assignment.tenant_account_id
                 AND account.bank_account_record_id = assignment.bank_account_record_id
                WHERE assignment.tenant_account_id = %s
                  AND account.bank_account_reference = %s
                """,
                (tenant_id, bank_account_reference),
            ).fetchone()[0]
        self.assertEqual(assignment_count, 0)


if __name__ == "__main__":
    unittest.main()
