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
        bank_account_reference = self._register_bank_account()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            self._set_tenant(connection, tenant_id)
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
                self._assignment_payload(
                    bank_account_reference,
                    valid_from="2026-08-31T00:00:00Z",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

        self.assertEqual(self._assignment_count(tenant_id, bank_account_reference), 0)

    def test_assignment_accepts_finite_chart_account_effective_at_relationship_start(self) -> None:
        """Historical assignment may bind the finite chart-account Entity effective then."""
        bank_account_reference = self._register_bank_account()

        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            self._set_tenant(connection, tenant_id)
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
            chart_valid_to, database_now = connection.execute(
                """
                UPDATE accounting_core.chart_account
                SET valid_from = TIMESTAMPTZ '2026-08-01 00:00:00+00',
                    valid_to = TIMESTAMPTZ '2026-09-01 00:00:00+00'
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND chart_account_code = %s
                RETURNING valid_to, clock_timestamp()
                """,
                (tenant_id, book_id, "110200"),
            ).fetchone()
        self.assertGreater(database_now, chart_valid_to)

        result = accept_bank_account_assignment(
            self._assignment_payload(
                bank_account_reference,
                valid_from="2026-08-31T00:00:00Z",
                valid_to="2026-09-01T00:00:00Z",
            ),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

        self.assertFalse(result["replayed"])
        self.assertEqual(self._assignment_count(tenant_id, bank_account_reference), 1)

    def _register_bank_account(self) -> str:
        """Register a fresh bank-account fixture and return its durable reference."""
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
        return bank_account_reference

    def _assignment_payload(
        self,
        bank_account_reference: str,
        *,
        valid_from: str,
        valid_to: str | None = None,
    ) -> dict[str, str]:
        """Return one assignment command with a fresh immutable command identity."""
        payload = {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "legal_entity_reference": self.case.policy.legal_entity_reference,
            "accounting_book_reference": self.case.policy.accounting_book_reference,
            "chart_account_code": "110200",
            "valid_from": valid_from,
            "assignment_idempotency_key": f"assign-chart-effective-time-{uuid.uuid4().hex}",
        }
        if valid_to is not None:
            payload["valid_to"] = valid_to
        return payload

    def _tenant_id(self, connection: psycopg.Connection[tuple[object, ...]]) -> object:
        """Return the tenant UUID for this isolated PostgreSQL fixture."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.tenant_account
            WHERE tenant_account_code = %s
            """,
            (self.case.policy.tenant_reference,),
        ).fetchone()[0]

    @staticmethod
    def _set_tenant(connection: psycopg.Connection[tuple[object, ...]], tenant_id: object) -> None:
        """Bind direct fixture SQL to the same FORCE-RLS tenant as the command path."""
        connection.execute(
            "SELECT set_config('app.tenant_account_id', %s, false)",
            (str(tenant_id),),
        )

    @staticmethod
    def _assignment_count(tenant_id: object, bank_account_reference: str) -> int:
        """Return durable assignment count for one fresh bank account."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            BankAssignmentChartAccountEffectiveTimeRedTests._set_tenant(connection, tenant_id)
            return connection.execute(
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


if __name__ == "__main__":
    unittest.main()
