"""PostgreSQL RED for accepted bank-account command-evidence rewrites."""

from __future__ import annotations

import unittest
import uuid
from uuid import UUID

import psycopg

from accounting_information_platform import accept_bank_account_record
from tests import test_postgres_posting as posting


class BankAccountRecordCommandEvidenceImmutabilityRedTests(unittest.TestCase):
    """Keep accepted bank-account currency and identifier evidence immutable."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Register one bank account through the supported command path."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.bank_account_reference = f"urn:cwl:bank_account:retained-account-evidence:{uuid.uuid4().hex}"
        document = accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier": f"acct-retained-account-evidence-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.bank_account_record_id = UUID(str(document["bank_account_record_id"]))

    def test_accepted_bank_account_currency_cannot_be_rewritten_in_place(self) -> None:
        """A canonical alternate currency cannot replace accepted command evidence."""
        with self._tenant_connection() as connection:
            original = self._record_evidence(connection)
            self.assertEqual(original[0], "KRW")

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE accounting_core.bank_account_record
                        SET account_currency_code = 'USD'
                        WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                          AND bank_account_record_id = %s
                        """,
                        (self.bank_account_record_id,),
                    )

            self.assertEqual(self._record_evidence(connection), original)

    def test_accepted_bank_account_identifier_hash_cannot_be_rewritten_in_place(self) -> None:
        """A well-formed alternate identifier hash cannot replace accepted evidence."""
        with self._tenant_connection() as connection:
            original = self._record_evidence(connection)
            replacement_hash = "sha256:" + ("f" * 64)
            if replacement_hash == original[1]:
                replacement_hash = "sha256:" + ("e" * 64)

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE accounting_core.bank_account_record
                        SET account_identifier_hash = %s
                        WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                          AND bank_account_record_id = %s
                        """,
                        (replacement_hash, self.bank_account_record_id),
                    )

            self.assertEqual(self._record_evidence(connection), original)

    def _record_evidence(
        self, connection: psycopg.Connection[tuple[object, ...]]
    ) -> tuple[str, str]:
        """Return the accepted currency and opaque identifier digest."""
        row = connection.execute(
            """
            SELECT account_currency_code, account_identifier_hash
            FROM accounting_core.bank_account_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_record_id = %s
            """,
            (self.bank_account_record_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        return str(row[0]), str(row[1])

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
