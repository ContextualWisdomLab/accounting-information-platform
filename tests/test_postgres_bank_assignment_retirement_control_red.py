"""PostgreSQL REDs for controlled bank-assignment retirement evidence."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from uuid import UUID

import psycopg

from accounting_information_platform import (
    accept_bank_account_assignment,
    accept_bank_account_record,
)
from tests import test_postgres_posting as posting


class BankAssignmentRetirementControlRedTests(unittest.TestCase):
    """Separate accepted finite validity from unaudited post-acceptance retirement writes."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated registered bank account for assignment commands."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.bank_account_reference = f"urn:cwl:bank_account:retirement:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"acct-retirement-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def test_initial_finite_valid_to_is_lawful_command_evidence(self) -> None:
        """A finite end supplied by the accepted command remains lawful planned validity."""
        valid_to = "2026-02-01T00:00:00Z"
        key = f"assign-retirement-finite-{uuid.uuid4().hex}"
        document = accept_bank_account_assignment(
            self._assignment_command(key, valid_to=valid_to),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        assignment_id = UUID(str(document["bank_account_assignment_id"]))

        with self._tenant_connection() as connection:
            stored_valid_to = connection.execute(
                """
                SELECT valid_to
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_assignment_id = %s
                """,
                (assignment_id,),
            ).fetchone()[0]

        self.assertEqual(
            stored_valid_to,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

    def test_open_assignment_cannot_be_closed_by_unaudited_direct_update(self) -> None:
        """Retirement must not be representable as a bare SQL valid_to rewrite."""
        assignment_id = self._accept_assignment(
            f"assign-retirement-open-{uuid.uuid4().hex}"
        )

        with self._tenant_connection() as connection:
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET valid_to = TIMESTAMPTZ '2026-02-01T00:00:00Z'
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (assignment_id,),
                )

    def test_finite_assignment_end_cannot_be_rewritten_without_new_evidence(self) -> None:
        """Changing an accepted finite end must require explicit retirement/amendment evidence."""
        key = f"assign-retirement-rewrite-{uuid.uuid4().hex}"
        document = accept_bank_account_assignment(
            self._assignment_command(key, valid_to="2026-03-01T00:00:00Z"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        assignment_id = UUID(str(document["bank_account_assignment_id"]))

        with self._tenant_connection() as connection:
            with self.assertRaises(psycopg.IntegrityError):
                connection.execute(
                    """
                    UPDATE accounting_core.bank_account_assignment
                    SET valid_to = TIMESTAMPTZ '2026-02-01T00:00:00Z'
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_account_assignment_id = %s
                    """,
                    (assignment_id,),
                )

    def _accept_assignment(self, idempotency_key: str) -> UUID:
        """Accept one open-ended assignment through the supported command path."""
        document = accept_bank_account_assignment(
            self._assignment_command(idempotency_key, valid_to=None),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return UUID(str(document["bank_account_assignment_id"]))

    def _assignment_command(self, idempotency_key: str, *, valid_to: str | None) -> dict[str, object]:
        """Build one assignment command while preserving the fixture's accounting scope."""
        command: dict[str, object] = {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "legal_entity_reference": self.case.policy.legal_entity_reference,
            "accounting_book_reference": self.case.policy.accounting_book_reference,
            "chart_account_code": "110200",
            "valid_from": "2026-01-01T00:00:00Z",
            "assignment_idempotency_key": idempotency_key,
        }
        if valid_to is not None:
            command["valid_to"] = valid_to
        return command

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
