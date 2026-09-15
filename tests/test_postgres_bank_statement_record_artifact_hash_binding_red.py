"""PostgreSQL RED for immutable statement-record to source-artifact hash binding."""

from __future__ import annotations

import hashlib
import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class BankStatementRecordArtifactHashBindingRedTests(unittest.TestCase):
    """Keep retained statement source hashes bound to the exact immutable artifact."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant fixture for direct PostgreSQL evidence checks."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_statement_record_cannot_claim_a_different_source_artifact_hash(self) -> None:
        """A retained statement cannot detach its source digest from the referenced artifact."""
        artifact_hash = self._fresh_hash()
        claimed_statement_hash = self._fresh_hash()
        statement_identity_reference = f"statement-{uuid.uuid4().hex}"
        ingestion_idempotency_key = f"ingest-{uuid.uuid4().hex}"
        self.assertNotEqual(artifact_hash, claimed_statement_hash)

        with self._tenant_connection() as connection:
            self._assert_artifact_hash_absent(connection, artifact_hash)
            self._assert_statement_hash_absent(connection, claimed_statement_hash)

            bank_account_record_id = connection.execute(
                """
                INSERT INTO accounting_core.bank_account_record (
                    tenant_account_id,
                    bank_account_reference,
                    account_currency_code,
                    account_identifier_hash
                )
                VALUES (
                    accounting_core.current_tenant_account_id(),
                    %s,
                    'KRW',
                    %s
                )
                RETURNING bank_account_record_id
                """,
                (
                    f"bank-account-{uuid.uuid4().hex}",
                    self._fresh_hash(),
                ),
            ).fetchone()[0]
            bank_statement_artifact_id = connection.execute(
                """
                INSERT INTO accounting_integration.bank_statement_artifact (
                    tenant_account_id,
                    source_artifact_hash,
                    artifact_store_reference,
                    artifact_byte_length
                )
                VALUES (
                    accounting_core.current_tenant_account_id(),
                    %s,
                    %s,
                    1
                )
                RETURNING bank_statement_artifact_id
                """,
                (
                    artifact_hash,
                    f"urn:cwl:bank_statement_artifact:{uuid.uuid4().hex}",
                ),
            ).fetchone()[0]
            self._assert_statement_identity_absent(
                connection,
                bank_account_record_id,
                statement_identity_reference,
            )
            self._assert_ingestion_key_absent(connection, ingestion_idempotency_key)

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_record (
                            tenant_account_id,
                            bank_account_record_id,
                            bank_statement_artifact_id,
                            message_definition_identifier,
                            statement_identity_reference,
                            source_artifact_hash,
                            normalized_payload_hash,
                            ingestion_idempotency_key
                        )
                        VALUES (
                            accounting_core.current_tenant_account_id(),
                            %s,
                            %s,
                            'camt.053.001.14',
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            bank_account_record_id,
                            bank_statement_artifact_id,
                            statement_identity_reference,
                            claimed_statement_hash,
                            self._fresh_hash(),
                            ingestion_idempotency_key,
                        ),
                    )

    def _fresh_hash(self) -> str:
        """Return a canonical SHA-256 value derived from fresh test entropy."""
        return "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()

    def _assert_artifact_hash_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        source_artifact_hash: str,
    ) -> None:
        """Exclude tenant-scoped artifact-hash uniqueness as an incidental rejection path."""
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

    def _assert_statement_hash_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        source_artifact_hash: str,
    ) -> None:
        """Exclude statement source-hash uniqueness as an incidental rejection path."""
        existing = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND source_artifact_hash = %s
            """,
            (source_artifact_hash,),
        ).fetchone()
        self.assertIsNone(existing)

    def _assert_statement_identity_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        bank_account_record_id: object,
        statement_identity_reference: str,
    ) -> None:
        """Exclude statement-identity uniqueness as an incidental rejection path."""
        existing = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_record_id = %s
              AND statement_identity_reference = %s
            """,
            (bank_account_record_id, statement_identity_reference),
        ).fetchone()
        self.assertIsNone(existing)

    def _assert_ingestion_key_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        ingestion_idempotency_key: str,
    ) -> None:
        """Exclude idempotency-key uniqueness as an incidental rejection path."""
        existing = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND ingestion_idempotency_key = %s
            """,
            (ingestion_idempotency_key,),
        ).fetchone()
        self.assertIsNone(existing)

    def _tenant_connection(self) -> psycopg.Connection[tuple[object, ...]]:
        """Open the fixture admin session with the tenant RLS context installed."""
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
