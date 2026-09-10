"""PostgreSQL REDs for immutable bank-assignment command evidence."""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from uuid import UUID

import psycopg

from accounting_information_platform import (
    accept_bank_account_assignment,
    accept_bank_account_record,
)
from tests import test_postgres_posting as posting


class BankAssignmentCommandEvidenceImmutabilityRedTests(unittest.TestCase):
    """Require persisted replay identity and command evidence to resist in-place rewrite."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated assignment through the supported command path."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.bank_account_reference = f"urn:cwl:bank_account:evidence:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"acct-evidence-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.assignment_idempotency_key = f"assign-evidence-{uuid.uuid4().hex}"
        document = accept_bank_account_assignment(
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
        self.assignment_id = UUID(str(document["bank_account_assignment_id"]))

    def test_assignment_command_hash_cannot_be_rewritten_in_place(self) -> None:
        """A different syntactically valid digest may not replace retained command evidence."""
        with self._tenant_connection() as connection:
            original_hash = connection.execute(
                """
                SELECT assignment_command_hash
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (self.assignment_id,),
            ).fetchone()[0]
            replacement_hash = "sha256:" + ("0" * 64)
            if replacement_hash == original_hash:
                replacement_hash = "sha256:" + ("1" * 64)

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET assignment_command_hash = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (replacement_hash, self.assignment_id),
                )

    def test_assignment_idempotency_key_cannot_be_rewritten_in_place(self) -> None:
        """Replay identity remains bound to the row created by the original command."""
        with self._tenant_connection() as connection:
            replacement_key = f"assign-evidence-rewrite-{uuid.uuid4().hex}"
            self.assertNotEqual(replacement_key, self.assignment_idempotency_key)

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET assignment_idempotency_key = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (replacement_key, self.assignment_id),
                )

    def test_assignment_primary_identity_cannot_be_rewritten_in_place(self) -> None:
        """The database-generated assignment identity remains stable for exact replay."""
        replacement_assignment_id = uuid.uuid4()
        self.assertNotEqual(replacement_assignment_id, self.assignment_id)

        with self._tenant_connection() as connection:
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET bank_account_assignment_id = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (replacement_assignment_id, self.assignment_id),
                )

    def test_assignment_valid_from_cannot_be_rewritten_in_place(self) -> None:
        """The accepted command start instant cannot drift while retained hash evidence stays fixed."""
        with self._tenant_connection() as connection:
            original_valid_from = connection.execute(
                """
                SELECT valid_from
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (self.assignment_id,),
            ).fetchone()[0]

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET valid_from = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (original_valid_from + timedelta(seconds=1), self.assignment_id),
                )

    def test_assignment_chart_account_identity_cannot_be_rebound_in_place(self) -> None:
        """The retained command cannot be detached from its accepted chart-account Entity."""
        with self._tenant_connection() as connection:
            replacement_chart_account_id = connection.execute(
                """
                SELECT chart_account.chart_account_id
                FROM accounting_core.bank_account_assignment AS assignment
                JOIN accounting_core.chart_account AS chart_account
                  ON chart_account.tenant_account_id = assignment.tenant_account_id
                 AND chart_account.accounting_book_id = assignment.accounting_book_id
                WHERE assignment.tenant_account_id = accounting_core.current_tenant_account_id()
                  AND assignment.bank_account_assignment_id = %s
                  AND chart_account.chart_account_code = '110100'
                """,
                (self.assignment_id,),
            ).fetchone()[0]
            original_chart_account_id = connection.execute(
                """
                SELECT chart_account_id
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (self.assignment_id,),
            ).fetchone()[0]
            self.assertNotEqual(replacement_chart_account_id, original_chart_account_id)

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET chart_account_id = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (replacement_chart_account_id, self.assignment_id),
                )

    def test_assignment_bank_account_identity_cannot_be_rebound_in_place(self) -> None:
        """The retained command cannot be detached from its accepted bank-account Entity."""
        replacement_reference = f"urn:cwl:bank_account:evidence:replacement:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": replacement_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"acct-evidence-replacement-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        with self._tenant_connection() as connection:
            replacement_bank_account_id = connection.execute(
                """
                SELECT bank_account_record_id
                FROM accounting_core.bank_account_record
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_reference = %s
                """,
                (replacement_reference,),
            ).fetchone()[0]
            original_bank_account_id = connection.execute(
                """
                SELECT bank_account_record_id
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (self.assignment_id,),
            ).fetchone()[0]
            self.assertNotEqual(replacement_bank_account_id, original_bank_account_id)

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET bank_account_record_id = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (replacement_bank_account_id, self.assignment_id),
                )

    def test_assignment_recorded_at_cannot_be_rewritten_in_place(self) -> None:
        """Database-owned creation time remains retained system-time evidence."""
        with self._tenant_connection() as connection:
            recorded_at = connection.execute(
                """
                SELECT recorded_at
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (self.assignment_id,),
            ).fetchone()[0]

            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET recorded_at = %s
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (recorded_at + timedelta(seconds=1), self.assignment_id),
                )

    def test_assignment_history_cannot_be_deleted_in_place(self) -> None:
        """Accepted effective-dated assignment history is retired by validity, not deletion."""
        with self._tenant_connection() as connection:
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    DELETE FROM accounting_core.bank_account_assignment
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (self.assignment_id,),
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
