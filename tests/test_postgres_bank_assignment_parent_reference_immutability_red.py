"""PostgreSQL RED for bank-assignment parent-reference evidence drift."""

from __future__ import annotations

import unittest
import uuid
from uuid import UUID

import psycopg

from accounting_information_platform import (
    accept_bank_account_assignment,
    accept_bank_account_record,
)
from tests import test_postgres_posting as posting


class BankAssignmentParentReferenceImmutabilityRedTests(unittest.TestCase):
    """Keep the durable bank reference bound to accepted assignment evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one bank account and assignment through supported command paths."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.bank_account_reference = f"urn:cwl:bank_account:retained-reference:{uuid.uuid4().hex}"
        bank_document = accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"acct-retained-reference-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.bank_account_record_id = UUID(str(bank_document["bank_account_record_id"]))
        self.assignment_idempotency_key = f"assign-retained-reference-{uuid.uuid4().hex}"
        assignment_document = accept_bank_account_assignment(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
                "assignment_idempotency_key": self.assignment_idempotency_key,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.assignment_id = UUID(str(assignment_document["bank_account_assignment_id"]))

    def test_linked_bank_account_reference_cannot_be_renamed_in_place(self) -> None:
        """A parent rename cannot detach replay output from the retained command hash."""
        replacement_reference = (
            f"urn:cwl:bank_account:retained-reference:replacement:{uuid.uuid4().hex}"
        )
        with self._tenant_connection() as connection:
            linked_row = connection.execute(
                """
                SELECT bank_account_record_id, assignment_command_hash
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (self.assignment_id,),
            ).fetchone()
            self.assertEqual(linked_row[0], self.bank_account_record_id)
            original_hash = linked_row[1]

            duplicate = connection.execute(
                """
                SELECT 1
                FROM accounting_core.bank_account_record
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_reference = %s
                """,
                (replacement_reference,),
            ).fetchone()
            self.assertIsNone(duplicate)

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE accounting_core.bank_account_record
                        SET bank_account_reference = %s
                        WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                          AND bank_account_record_id = %s
                        """,
                        (replacement_reference, self.bank_account_record_id),
                    )

            retained_hash = connection.execute(
                """
                SELECT assignment_command_hash
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (self.assignment_id,),
            ).fetchone()[0]
            self.assertEqual(retained_hash, original_hash)

    def _tenant_connection(self) -> psycopg.Connection[tuple[object, ...]]:
        """Open a direct session with the fixture tenant RLS context installed."""
        connection = psycopg.connect(posting.DATABASE_URL)
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
        return connection


if __name__ == "__main__":
    unittest.main()
