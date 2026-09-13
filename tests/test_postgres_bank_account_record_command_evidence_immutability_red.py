"""PostgreSQL RED for accepted bank-account registration evidence retention."""

from __future__ import annotations

import unittest
import uuid
from uuid import UUID

import psycopg

from accounting_information_platform import accept_bank_account_record
from tests import test_postgres_posting as posting


class BankAccountRecordCommandEvidenceImmutabilityRedTests(unittest.TestCase):
    """Keep accepted bank-account registration identity and evidence retained."""

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

    def test_accepted_bank_account_reference_cannot_be_rewritten_in_place(self) -> None:
        """Durable replay identity cannot be renamed after accepted registration."""
        replacement_reference = (
            f"urn:cwl:bank_account:retained-account-evidence:replacement:{uuid.uuid4().hex}"
        )
        with self._tenant_connection() as connection:
            original_reference = self._record_reference(connection)
            self.assertEqual(original_reference, self.bank_account_reference)
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

            self.assertEqual(self._record_reference(connection), original_reference)

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

    def test_accepted_bank_account_record_id_cannot_be_rewritten_in_place(self) -> None:
        """Database-generated registration identity cannot be replaced after acceptance."""
        replacement_record_id = uuid.uuid4()
        with self._tenant_connection() as connection:
            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE accounting_core.bank_account_record
                        SET bank_account_record_id = %s
                        WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                          AND bank_account_record_id = %s
                        """,
                        (replacement_record_id, self.bank_account_record_id),
                    )

            self.assertEqual(self._record_count(connection, self.bank_account_record_id), 1)
            self.assertEqual(self._record_count(connection, replacement_record_id), 0)

    def test_accepted_bank_account_recorded_at_cannot_be_rewritten_in_place(self) -> None:
        """PostgreSQL-owned registration system time cannot be rewritten after acceptance."""
        with self._tenant_connection() as connection:
            original_recorded_at = connection.execute(
                """
                SELECT recorded_at
                FROM accounting_core.bank_account_record
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_record_id = %s
                """,
                (self.bank_account_record_id,),
            ).fetchone()[0]

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE accounting_core.bank_account_record
                        SET recorded_at = recorded_at + interval '1 second'
                        WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                          AND bank_account_record_id = %s
                        """,
                        (self.bank_account_record_id,),
                    )

            retained_recorded_at = connection.execute(
                """
                SELECT recorded_at
                FROM accounting_core.bank_account_record
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_record_id = %s
                """,
                (self.bank_account_record_id,),
            ).fetchone()[0]
            self.assertEqual(retained_recorded_at, original_recorded_at)

    def test_accepted_bank_account_record_cannot_be_physically_deleted(self) -> None:
        """An otherwise unreferenced accepted registration remains retained evidence."""
        with self._tenant_connection() as connection:
            original = self._record_evidence(connection)

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        DELETE FROM accounting_core.bank_account_record
                        WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                          AND bank_account_record_id = %s
                        """,
                        (self.bank_account_record_id,),
                    )

            self.assertEqual(self._record_evidence(connection), original)

    def _record_reference(self, connection: psycopg.Connection[tuple[object, ...]]) -> str:
        """Return the durable bank-account replay identity."""
        row = connection.execute(
            """
            SELECT bank_account_reference
            FROM accounting_core.bank_account_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_record_id = %s
            """,
            (self.bank_account_record_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        return str(row[0])

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

    def _record_count(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        bank_account_record_id: UUID,
    ) -> int:
        """Count one candidate registration identity inside the tenant boundary."""
        return int(
            connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.bank_account_record
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_account_record_id = %s
                """,
                (bank_account_record_id,),
            ).fetchone()[0]
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
