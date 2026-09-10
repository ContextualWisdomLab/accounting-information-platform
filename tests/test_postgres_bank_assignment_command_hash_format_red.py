"""PostgreSQL REDs for canonical bank-assignment command-hash syntax."""

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


class BankAssignmentCommandHashFormatRedTests(unittest.TestCase):
    """Require retained assignment command hashes to be exact canonical SHA-256 evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create lawful parent identities and an unassigned bank account for direct admission."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        seed_bank_reference = f"urn:cwl:bank_account:hash-seed:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": seed_bank_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"acct-hash-seed-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        seed_document = accept_bank_account_assignment(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": seed_bank_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
                "assignment_idempotency_key": f"assign-hash-seed-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.seed_assignment_id = UUID(str(seed_document["bank_account_assignment_id"]))

        self.target_bank_reference = f"urn:cwl:bank_account:hash-target:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.target_bank_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"acct-hash-target-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def test_short_sha256_assignment_command_hash_is_rejected_on_insert(self) -> None:
        """A prefix-only or short digest is not canonical SHA-256 command evidence."""
        with self._tenant_connection() as connection:
            with self.assertRaises(psycopg.IntegrityError):
                self._insert_target_assignment(connection, "sha256:" + ("0" * 63))

    def test_non_hex_assignment_command_hash_is_rejected_on_insert(self) -> None:
        """Sixty-four non-hex characters cannot masquerade as SHA-256 evidence."""
        with self._tenant_connection() as connection:
            with self.assertRaises(psycopg.IntegrityError):
                self._insert_target_assignment(connection, "sha256:" + ("g" * 64))

    def _insert_target_assignment(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        assignment_command_hash: str,
    ) -> None:
        """Insert an otherwise lawful assignment so only digest syntax can explain rejection."""
        parent = connection.execute(
            """
            SELECT
                assignment.legal_entity_id,
                assignment.accounting_book_id,
                assignment.chart_account_id,
                assignment.valid_from,
                assignment.valid_to
            FROM accounting_core.bank_account_assignment AS assignment
            WHERE assignment.tenant_account_id = accounting_core.current_tenant_account_id()
              AND assignment.bank_account_assignment_id = %s
            """,
            (self.seed_assignment_id,),
        ).fetchone()
        self.assertIsNotNone(parent)
        legal_entity_id, accounting_book_id, chart_account_id, valid_from, valid_to = parent
        target_bank_account_id = connection.execute(
            """
            SELECT bank_account_record_id
            FROM accounting_core.bank_account_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_reference = %s
            """,
            (self.target_bank_reference,),
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
            )
            VALUES (
                accounting_core.current_tenant_account_id(),
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                target_bank_account_id,
                legal_entity_id,
                accounting_book_id,
                chart_account_id,
                valid_from,
                valid_to,
                f"assign-hash-target-{uuid.uuid4().hex}",
                assignment_command_hash,
            ),
        )

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
