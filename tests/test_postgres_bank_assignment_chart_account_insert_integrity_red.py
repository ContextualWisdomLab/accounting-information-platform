"""PostgreSQL RED for chart-account containment when a bank assignment is created."""

from __future__ import annotations

from datetime import timedelta
import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BankAssignmentChartAccountInsertIntegrityRedTests(unittest.TestCase):
    """Require new bank assignments to start and end inside the exact chart-account Entity."""

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

    def test_insert_rejects_assignment_start_before_chart_account_start(self) -> None:
        """A newly inserted assignment may not predate its exact chart-account Entity."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            fixture = self._insert_parent_fixture(connection, "insert-before-chart")
            invalid_start = fixture["chart_valid_from"] - timedelta(days=1)
            valid_end = fixture["chart_valid_from"] + timedelta(days=2)

            self.assertLess(fixture["book_valid_from"], invalid_start)
            self.assertLess(invalid_start, fixture["chart_valid_from"])
            self.assertLess(valid_end, fixture["chart_valid_to"])
            self.assertLess(valid_end, fixture["book_valid_to"])

            with self.assertRaises(psycopg.IntegrityError):
                self._insert_assignment(
                    connection,
                    fixture=fixture,
                    valid_from=invalid_start,
                    valid_to=valid_end,
                    fixture_name="insert-before-chart",
                )
            connection.rollback()

    def test_insert_rejects_assignment_end_after_chart_account_end(self) -> None:
        """A newly inserted assignment may not outlive its exact chart-account Entity."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            fixture = self._insert_parent_fixture(connection, "insert-after-chart")
            valid_start = fixture["chart_valid_from"] + timedelta(days=2)
            invalid_end = fixture["chart_valid_to"] + timedelta(days=1)

            self.assertLess(fixture["chart_valid_from"], valid_start)
            self.assertLess(valid_start, fixture["chart_valid_to"])
            self.assertLess(fixture["chart_valid_to"], invalid_end)
            self.assertLess(invalid_end, fixture["book_valid_to"])

            with self.assertRaises(psycopg.IntegrityError):
                self._insert_assignment(
                    connection,
                    fixture=fixture,
                    valid_from=valid_start,
                    valid_to=invalid_end,
                    fixture_name="insert-after-chart",
                )
            connection.rollback()

    def _insert_parent_fixture(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        fixture_name: str,
    ) -> dict[str, object]:
        """Insert one Book, chart-account Entity, and bank account with isolated finite intervals."""
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
        book_valid_from = anchor - timedelta(days=10)
        book_valid_to = anchor + timedelta(days=10)
        chart_valid_from = anchor - timedelta(days=8)
        chart_valid_to = anchor + timedelta(days=8)
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
            ) VALUES (%s, %s, 'management', %s, 'KRW', %s, %s)
            RETURNING accounting_book_id
            """,
            (
                tenant_id,
                legal_entity_id,
                book_reference,
                book_valid_from,
                book_valid_to,
            ),
        ).fetchone()[0]
        chart_account_id = connection.execute(
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
            ) VALUES (%s, %s, %s, %s, 'debit', %s, %s, 'asset')
            RETURNING chart_account_id
            """,
            (
                tenant_id,
                book_id,
                f"19{uuid.uuid4().int % 10000:04d}",
                f"Temporal {fixture_name}",
                chart_valid_from,
                chart_valid_to,
            ),
        ).fetchone()[0]
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
            "chart_account_id": chart_account_id,
            "bank_account_id": bank_account_id,
            "book_valid_from": book_valid_from,
            "book_valid_to": book_valid_to,
            "chart_valid_from": chart_valid_from,
            "chart_valid_to": chart_valid_to,
        }

    @staticmethod
    def _insert_assignment(
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        fixture: dict[str, object],
        valid_from: object,
        valid_to: object,
        fixture_name: str,
    ) -> None:
        """Insert the hostile assignment while leaving all parent rows unchanged."""
        connection.execute(
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
            """,
            (
                fixture["tenant_id"],
                fixture["bank_account_id"],
                fixture["legal_entity_id"],
                fixture["book_id"],
                fixture["chart_account_id"],
                valid_from,
                valid_to,
                f"{fixture_name}-{uuid.uuid4().hex}",
                "sha256:" + uuid.uuid4().hex.ljust(64, "0"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
