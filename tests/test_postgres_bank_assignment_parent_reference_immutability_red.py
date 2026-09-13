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
    """Keep durable parent references bound to accepted assignment evidence."""

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
        """A bank-account rename cannot detach replay output from retained evidence."""
        replacement_reference = (
            f"urn:cwl:bank_account:retained-reference:replacement:{uuid.uuid4().hex}"
        )
        with self._tenant_connection() as connection:
            self._assert_reference_rewrite_rejected(
                connection,
                table="bank_account_record",
                id_column="bank_account_record_id",
                id_value=self.bank_account_record_id,
                reference_column="bank_account_reference",
                replacement_reference=replacement_reference,
            )

    def test_linked_legal_entity_reference_cannot_be_renamed_in_place(self) -> None:
        """A legal-entity rename cannot detach replay output from retained evidence."""
        replacement_reference = f"LE-RETAINED-{uuid.uuid4().hex}"
        with self._tenant_connection() as connection:
            legal_entity_id, _book_id, _chart_id = self._assignment_parent_ids(connection)
            self._assert_reference_rewrite_rejected(
                connection,
                table="legal_entity_record",
                id_column="legal_entity_id",
                id_value=legal_entity_id,
                reference_column="legal_entity_code",
                replacement_reference=replacement_reference,
            )

    def test_linked_accounting_book_reference_cannot_be_renamed_in_place(self) -> None:
        """A Book rename cannot detach replay output from retained evidence."""
        replacement_reference = f"BOOK-RETAINED-{uuid.uuid4().hex}"
        with self._tenant_connection() as connection:
            _legal_entity_id, book_id, _chart_id = self._assignment_parent_ids(connection)
            self._assert_reference_rewrite_rejected(
                connection,
                table="accounting_book",
                id_column="accounting_book_id",
                id_value=book_id,
                reference_column="book_name",
                replacement_reference=replacement_reference,
            )

    def test_linked_chart_account_code_cannot_be_renamed_in_place(self) -> None:
        """A chart-account rename cannot detach replay output from retained evidence."""
        replacement_reference = f"199{uuid.uuid4().int % 1_000_000:06d}"
        with self._tenant_connection() as connection:
            _legal_entity_id, _book_id, chart_id = self._assignment_parent_ids(connection)
            self._assert_reference_rewrite_rejected(
                connection,
                table="chart_account",
                id_column="chart_account_id",
                id_value=chart_id,
                reference_column="chart_account_code",
                replacement_reference=replacement_reference,
            )

    def _assignment_parent_ids(
        self, connection: psycopg.Connection[tuple[object, ...]]
    ) -> tuple[UUID, UUID, UUID]:
        """Return the exact legal-entity, Book, and chart parents of the assignment."""
        row = connection.execute(
            """
            SELECT legal_entity_id, accounting_book_id, chart_account_id
            FROM accounting_core.bank_account_assignment
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_assignment_id = %s
            """,
            (self.assignment_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        return UUID(str(row[0])), UUID(str(row[1])), UUID(str(row[2]))

    def _assert_reference_rewrite_rejected(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        table: str,
        id_column: str,
        id_value: UUID,
        reference_column: str,
        replacement_reference: str,
    ) -> None:
        """Require one fresh parent-reference rewrite to fail without changing evidence."""
        original_hash = connection.execute(
            """
            SELECT assignment_command_hash
            FROM accounting_core.bank_account_assignment
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_assignment_id = %s
            """,
            (self.assignment_id,),
        ).fetchone()[0]
        duplicate = connection.execute(
            f"""
            SELECT 1
            FROM accounting_core.{table}
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND {reference_column} = %s
            """,
            (replacement_reference,),
        ).fetchone()
        self.assertIsNone(duplicate)

        with self.assertRaises(psycopg.IntegrityError):
            with connection.transaction():
                connection.execute(
                    f"""
                    UPDATE accounting_core.{table}
                    SET {reference_column} = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND {id_column} = %s
                    """,
                    (replacement_reference, id_value),
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
