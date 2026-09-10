"""PostgreSQL RED for server-owned bank-account registration provenance."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BankAccountRecordServerOwnedProvenanceRedTests(unittest.TestCase):
    """Keep accepted registration identity and system time database-owned at admission."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant fixture for direct PostgreSQL admission checks."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_direct_insert_cannot_claim_database_owned_record_id(self) -> None:
        """A caller-supplied UUID cannot become the accepted registration identity."""
        forged_record_id = uuid.uuid4()
        reference = f"urn:cwl:bank_account:forged-record-id:{uuid.uuid4().hex}"

        with self._tenant_connection() as connection:
            try:
                with connection.transaction():
                    retained_record_id = connection.execute(
                        """
                        INSERT INTO accounting_core.bank_account_record (
                            bank_account_record_id,
                            tenant_account_id,
                            bank_account_reference,
                            account_currency_code,
                            account_identifier_hash
                        )
                        VALUES (
                            %s,
                            accounting_core.current_tenant_account_id(),
                            %s,
                            'KRW',
                            %s
                        )
                        RETURNING bank_account_record_id
                        """,
                        (forged_record_id, reference, "sha256:" + ("a" * 64)),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_record_id, forged_record_id)

    def test_direct_insert_cannot_claim_postgresql_owned_recorded_at(self) -> None:
        """A caller-supplied timestamp cannot become registration system-time evidence."""
        forged_recorded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        reference = f"urn:cwl:bank_account:forged-recorded-at:{uuid.uuid4().hex}"

        with self._tenant_connection() as connection:
            try:
                with connection.transaction():
                    retained_recorded_at = connection.execute(
                        """
                        INSERT INTO accounting_core.bank_account_record (
                            tenant_account_id,
                            bank_account_reference,
                            account_currency_code,
                            account_identifier_hash,
                            recorded_at
                        )
                        VALUES (
                            accounting_core.current_tenant_account_id(),
                            %s,
                            'KRW',
                            %s,
                            %s
                        )
                        RETURNING recorded_at
                        """,
                        (reference, "sha256:" + ("b" * 64), forged_recorded_at),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_recorded_at, forged_recorded_at)

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
