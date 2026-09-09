"""PostgreSQL RED for temporal containment of bank-account assignments."""

from __future__ import annotations

from datetime import timedelta
import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BankAssignmentBookIntervalIntegrityRedTests(unittest.TestCase):
    """Require a bank-account assignment interval to remain inside its Accounting Book."""

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

    def test_book_end_cannot_strand_existing_bank_assignment(self) -> None:
        """Shortening a book may not leave an assignment effective after that book ends."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.case.tenant_id),),
            )
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s
                  AND legal_entity_code = %s
                  AND valid_to IS NULL
                """,
                (
                    self.case.tenant_id,
                    self.case.policy.legal_entity_reference,
                ),
            ).fetchone()[0]
            anchor = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            book_valid_from = anchor - timedelta(days=10)
            book_valid_to = anchor + timedelta(days=10)
            chart_valid_to = anchor + timedelta(days=2)
            assignment_valid_from = anchor - timedelta(days=1)
            assignment_valid_to = anchor + timedelta(days=5)
            shortened_book_valid_to = anchor + timedelta(days=3)
            book_reference = f"urn:cwl:accounting_book:assignment_parent_{uuid.uuid4().hex}"
            bank_account_reference = f"urn:cwl:bank_account:assignment_parent_{uuid.uuid4().hex}"
            assignment_key = f"assignment-parent-{uuid.uuid4().hex}"

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
                    self.case.tenant_id,
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
                ) VALUES (%s, %s, '190901', 'Temporal assignment cash', 'debit', %s, %s, 'asset')
                RETURNING chart_account_id
                """,
                (
                    self.case.tenant_id,
                    book_id,
                    book_valid_from,
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
                    self.case.tenant_id,
                    bank_account_reference,
                    "sha256:" + "8" * 64,
                ),
            ).fetchone()[0]
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
                    self.case.tenant_id,
                    bank_account_id,
                    legal_entity_id,
                    book_id,
                    chart_account_id,
                    assignment_valid_from,
                    assignment_valid_to,
                    assignment_key,
                    "sha256:" + "9" * 64,
                ),
            )

            self.assertLess(chart_valid_to, shortened_book_valid_to)
            self.assertLess(shortened_book_valid_to, assignment_valid_to)
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.accounting_book
                    SET valid_to = %s
                    WHERE tenant_account_id = %s
                      AND accounting_book_id = %s
                    """,
                    (
                        shortened_book_valid_to,
                        self.case.tenant_id,
                        book_id,
                    ),
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
