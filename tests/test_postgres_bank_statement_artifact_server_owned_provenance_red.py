"""PostgreSQL RED for server-owned bank-statement artifact provenance."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import unittest
import uuid
from uuid import UUID

import psycopg

from tests import test_postgres_posting as posting


class BankStatementArtifactServerOwnedProvenanceRedTests(unittest.TestCase):
    """Keep immutable statement-artifact identity and system time server-owned at admission."""

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

    def test_direct_insert_cannot_claim_database_owned_artifact_id(self) -> None:
        """A caller-supplied UUID cannot become immutable artifact identity evidence."""
        supplied_artifact_id = uuid.uuid4()
        source_hash = self._fresh_source_hash()
        locator = f"urn:cwl:bank_statement_artifact:{uuid.uuid4().hex}"

        with self._tenant_connection() as connection:
            self._assert_source_hash_absent(connection, source_hash)
            self.assertEqual(self._artifact_id_count(connection, supplied_artifact_id), 0)

            try:
                with connection.transaction():
                    retained_artifact_id = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_artifact (
                            bank_statement_artifact_id,
                            tenant_account_id,
                            source_artifact_hash,
                            artifact_store_reference,
                            artifact_byte_length
                        )
                        VALUES (
                            %s,
                            accounting_core.current_tenant_account_id(),
                            %s,
                            %s,
                            1
                        )
                        RETURNING bank_statement_artifact_id
                        """,
                        (supplied_artifact_id, source_hash, locator),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_artifact_id, supplied_artifact_id)

    def test_direct_insert_cannot_claim_postgresql_owned_recorded_at(self) -> None:
        """A caller-supplied timestamp cannot become immutable artifact system-time evidence."""
        supplied_recorded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        source_hash = self._fresh_source_hash()
        locator = f"urn:cwl:bank_statement_artifact:{uuid.uuid4().hex}"

        with self._tenant_connection() as connection:
            self._assert_source_hash_absent(connection, source_hash)

            try:
                with connection.transaction():
                    retained_recorded_at = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_artifact (
                            tenant_account_id,
                            source_artifact_hash,
                            artifact_store_reference,
                            artifact_byte_length,
                            recorded_at
                        )
                        VALUES (
                            accounting_core.current_tenant_account_id(),
                            %s,
                            %s,
                            1,
                            %s
                        )
                        RETURNING recorded_at
                        """,
                        (source_hash, locator, supplied_recorded_at),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_recorded_at, supplied_recorded_at)

    def _fresh_source_hash(self) -> str:
        """Return a canonical SHA-256 value derived from fresh test entropy."""
        return "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()

    def _assert_source_hash_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        source_artifact_hash: str,
    ) -> None:
        """Exclude existing source-hash uniqueness as an incidental rejection path."""
        existing = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_artifact
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND source_artifact_hash = %s
            """,
            (source_artifact_hash,),
        ).fetchone()
        self.assertIsNone(existing)

    def _artifact_id_count(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        bank_statement_artifact_id: UUID,
    ) -> int:
        """Count a proposed artifact UUID before the direct admission attempt."""
        return int(
            connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.bank_statement_artifact
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_statement_artifact_id = %s
                """,
                (bank_statement_artifact_id,),
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
