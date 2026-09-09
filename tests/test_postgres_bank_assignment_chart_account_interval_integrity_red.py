"""PostgreSQL RED for chart-account containment of bank-account assignments."""

from __future__ import annotations

from datetime import timedelta
import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BankAssignmentChartAccountIntervalIntegrityRedTests(unittest.TestCase):
    """Require bank-account assignments to stay inside their chart-account interval."""

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

    def test_chart_account_end_cannot_strand_existing_bank_assignment(self) -> None:
        """Shortening a chart account may not leave its assignment effective afterward."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, legal_entity_id = self._tenant_scope(connection)
            anchor = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            book_valid_from = anchor - timedelta(days=10)
            book_valid_to = anchor + timedelta(days=10)
            chart_valid_from = anchor - timedelta(days=8)
            chart_valid_to = anchor + timedelta(days=8)
            assignment_valid_from = anchor - timedelta(days=1)
            assignment_valid_to = anchor + timedelta(days=5)
            shortened_chart_valid_to = anchor + timedelta(days=3)
            book_id, chart_account_id, assignment_id = self._insert_assignment_fixture(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_valid_from=book_valid_from,
                book_valid_to=book_valid_to,
                chart_valid_from=chart_valid_from,
                chart_valid_to=chart_valid_to,
                assignment_valid_from=assignment_valid_from,
                assignment_valid_to=assignment_valid_to,
                fixture_name="chart-parent-end",
            )

            self.assertLess(assignment_valid_from, shortened_chart_valid_to)
            self.assertLess(shortened_chart_valid_to, assignment_valid_to)
            self.assertLess(assignment_valid_to, book_valid_to)
            self.assertIsNotNone(book_id)
            self.assertIsNotNone(assignment_id)
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.chart_account
                    SET valid_to = %s
                    WHERE tenant_account_id = %s
                      AND chart_account_id = %s
                    """,
                    (
                        shortened_chart_valid_to,
                        tenant_id,
                        chart_account_id,
                    ),
                )
            connection.rollback()

    def test_chart_account_start_cannot_strand_existing_bank_assignment(self) -> None:
        """Moving a chart-account start forward may not strand its assignment before it."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, legal_entity_id = self._tenant_scope(connection)
            anchor = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            book_valid_from = anchor - timedelta(days=10)
            book_valid_to = anchor + timedelta(days=10)
            chart_valid_from = anchor - timedelta(days=8)
            chart_valid_to = anchor + timedelta(days=8)
            assignment_valid_from = anchor - timedelta(days=5)
            assignment_valid_to = anchor + timedelta(days=5)
            moved_chart_valid_from = anchor - timedelta(days=3)
            _, chart_account_id, assignment_id = self._insert_assignment_fixture(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_valid_from=book_valid_from,
                book_valid_to=book_valid_to,
                chart_valid_from=chart_valid_from,
                chart_valid_to=chart_valid_to,
                assignment_valid_from=assignment_valid_from,
                assignment_valid_to=assignment_valid_to,
                fixture_name="chart-parent-start",
            )

            self.assertLess(book_valid_from, assignment_valid_from)
            self.assertLess(assignment_valid_from, moved_chart_valid_from)
            self.assertLess(moved_chart_valid_from, assignment_valid_to)
            self.assertLess(assignment_valid_to, book_valid_to)
            self.assertIsNotNone(assignment_id)
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.chart_account
                    SET valid_from = %s
                    WHERE tenant_account_id = %s
                      AND chart_account_id = %s
                    """,
                    (
                        moved_chart_valid_from,
                        tenant_id,
                        chart_account_id,
                    ),
                )
            connection.rollback()

    def test_assignment_end_cannot_extend_beyond_existing_chart_account_end(self) -> None:
        """Extending an assignment end may not make it outlive its chart account."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, legal_entity_id = self._tenant_scope(connection)
            anchor = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            book_valid_from = anchor - timedelta(days=10)
            book_valid_to = anchor + timedelta(days=10)
            chart_valid_from = anchor - timedelta(days=8)
            chart_valid_to = anchor + timedelta(days=6)
            assignment_valid_from = anchor - timedelta(days=1)
            assignment_valid_to = anchor + timedelta(days=5)
            extended_assignment_valid_to = anchor + timedelta(days=7)
            _, _, assignment_id = self._insert_assignment_fixture(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_valid_from=book_valid_from,
                book_valid_to=book_valid_to,
                chart_valid_from=chart_valid_from,
                chart_valid_to=chart_valid_to,
                assignment_valid_from=assignment_valid_from,
                assignment_valid_to=assignment_valid_to,
                fixture_name="chart-assignment-end",
            )

            self.assertLess(chart_valid_to, extended_assignment_valid_to)
            self.assertLess(extended_assignment_valid_to, book_valid_to)
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET valid_to = %s
                    WHERE tenant_account_id = %s
                      AND bank_account_assignment_id = %s
                    """,
                    (
                        extended_assignment_valid_to,
                        tenant_id,
                        assignment_id,
                    ),
                )
            connection.rollback()

    def test_assignment_start_cannot_move_before_existing_chart_account_start(self) -> None:
        """Moving an assignment start backward may not predate its chart account."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, legal_entity_id = self._tenant_scope(connection)
            anchor = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            book_valid_from = anchor - timedelta(days=10)
            book_valid_to = anchor + timedelta(days=10)
            chart_valid_from = anchor - timedelta(days=8)
            chart_valid_to = anchor + timedelta(days=8)
            assignment_valid_from = anchor - timedelta(days=1)
            assignment_valid_to = anchor + timedelta(days=5)
            moved_assignment_valid_from = anchor - timedelta(days=9)
            _, _, assignment_id = self._insert_assignment_fixture(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_valid_from=book_valid_from,
                book_valid_to=book_valid_to,
                chart_valid_from=chart_valid_from,
                chart_valid_to=chart_valid_to,
                assignment_valid_from=assignment_valid_from,
                assignment_valid_to=assignment_valid_to,
                fixture_name="chart-assignment-start",
            )

            self.assertLess(book_valid_from, moved_assignment_valid_from)
            self.assertLess(moved_assignment_valid_from, chart_valid_from)
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET valid_from = %s
                    WHERE tenant_account_id = %s
                      AND bank_account_assignment_id = %s
                    """,
                    (
                        moved_assignment_valid_from,
                        tenant_id,
                        assignment_id,
                    ),
                )
            connection.rollback()

    def _tenant_scope(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
    ) -> tuple[object, object]:
        """Bind tenant RLS and return the isolated tenant and legal-entity UUIDs."""
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
        return tenant_id, legal_entity_id

    def _insert_assignment_fixture(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        tenant_id: object,
        legal_entity_id: object,
        book_valid_from: object,
        book_valid_to: object,
        chart_valid_from: object,
        chart_valid_to: object,
        assignment_valid_from: object,
        assignment_valid_to: object,
        fixture_name: str,
    ) -> tuple[object, object, object]:
        """Insert one lawful finite Book/chart-account/assignment relationship fixture."""
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
        assignment_id = connection.execute(
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
                tenant_id,
                bank_account_id,
                legal_entity_id,
                book_id,
                chart_account_id,
                assignment_valid_from,
                assignment_valid_to,
                f"{fixture_name}-{uuid.uuid4().hex}",
                "sha256:" + uuid.uuid4().hex.ljust(64, "0"),
            ),
        ).fetchone()[0]
        return book_id, chart_account_id, assignment_id


if __name__ == "__main__":
    unittest.main()
