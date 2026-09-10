"""PostgreSQL RED for server-owned bank-statement record provenance."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import unittest
import uuid
from uuid import UUID

import psycopg

from tests import test_postgres_posting as posting


class BankStatementRecordServerOwnedProvenanceRedTests(unittest.TestCase):
    """Keep immutable statement-record identity and system time server-owned at admission."""

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

    def test_direct_insert_cannot_claim_database_owned_statement_record_id(self) -> None:
        """A caller-supplied UUID cannot become immutable statement-record identity evidence."""
        supplied_statement_id = uuid.uuid4()

        with self._tenant_connection() as connection:
            statement_values = self._create_lawful_parents(connection)
            self.assertEqual(self._statement_id_count(connection, supplied_statement_id), 0)
            self._assert_statement_uniqueness_absent(connection, statement_values)

            try:
                with connection.transaction():
                    retained_statement_id = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_record (
                            bank_statement_record_id,
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
                            %s,
                            accounting_core.current_tenant_account_id(),
                            %s,
                            %s,
                            'camt.053.001.14',
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        RETURNING bank_statement_record_id
                        """,
                        (
                            supplied_statement_id,
                            statement_values["bank_account_record_id"],
                            statement_values["bank_statement_artifact_id"],
                            statement_values["statement_identity_reference"],
                            statement_values["source_artifact_hash"],
                            statement_values["normalized_payload_hash"],
                            statement_values["ingestion_idempotency_key"],
                        ),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_statement_id, supplied_statement_id)

    def test_direct_insert_cannot_claim_postgresql_owned_statement_recorded_at(self) -> None:
        """A caller timestamp cannot become immutable statement-record system-time evidence."""
        supplied_recorded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

        with self._tenant_connection() as connection:
            statement_values = self._create_lawful_parents(connection)
            self._assert_statement_uniqueness_absent(connection, statement_values)

            try:
                with connection.transaction():
                    retained_recorded_at = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_record (
                            tenant_account_id,
                            bank_account_record_id,
                            bank_statement_artifact_id,
                            message_definition_identifier,
                            statement_identity_reference,
                            source_artifact_hash,
                            normalized_payload_hash,
                            ingestion_idempotency_key,
                            recorded_at
                        )
                        VALUES (
                            accounting_core.current_tenant_account_id(),
                            %s,
                            %s,
                            'camt.053.001.14',
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        RETURNING recorded_at
                        """,
                        (
                            statement_values["bank_account_record_id"],
                            statement_values["bank_statement_artifact_id"],
                            statement_values["statement_identity_reference"],
                            statement_values["source_artifact_hash"],
                            statement_values["normalized_payload_hash"],
                            statement_values["ingestion_idempotency_key"],
                            supplied_recorded_at,
                        ),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_recorded_at, supplied_recorded_at)

    def _create_lawful_parents(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
    ) -> dict[str, object]:
        """Create fresh parent rows while omitting every server-owned provenance column."""
        bank_account_reference = f"bank-account-{uuid.uuid4().hex}"
        source_artifact_hash = self._fresh_hash()
        statement_identity_reference = f"statement-{uuid.uuid4().hex}"
        ingestion_idempotency_key = f"ingest-{uuid.uuid4().hex}"

        self._assert_bank_account_reference_absent(connection, bank_account_reference)
        self._assert_artifact_hash_absent(connection, source_artifact_hash)

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
            (bank_account_reference, self._fresh_hash()),
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
                source_artifact_hash,
                f"urn:cwl:bank_statement_artifact:{uuid.uuid4().hex}",
            ),
        ).fetchone()[0]

        return {
            "bank_account_record_id": bank_account_record_id,
            "bank_statement_artifact_id": bank_statement_artifact_id,
            "statement_identity_reference": statement_identity_reference,
            "source_artifact_hash": source_artifact_hash,
            "normalized_payload_hash": self._fresh_hash(),
            "ingestion_idempotency_key": ingestion_idempotency_key,
        }

    def _assert_statement_uniqueness_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        statement_values: dict[str, object],
    ) -> None:
        """Exclude every statement-table UNIQUE key as an incidental rejection path."""
        source_row = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND source_artifact_hash = %s
            """,
            (statement_values["source_artifact_hash"],),
        ).fetchone()
        self.assertIsNone(source_row)

        identity_row = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_record_id = %s
              AND statement_identity_reference = %s
            """,
            (
                statement_values["bank_account_record_id"],
                statement_values["statement_identity_reference"],
            ),
        ).fetchone()
        self.assertIsNone(identity_row)

        ingestion_row = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND ingestion_idempotency_key = %s
            """,
            (statement_values["ingestion_idempotency_key"],),
        ).fetchone()
        self.assertIsNone(ingestion_row)

    def _assert_bank_account_reference_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        bank_account_reference: str,
    ) -> None:
        """Exclude tenant/reference uniqueness as a parent-admission failure path."""
        existing = connection.execute(
            """
            SELECT 1
            FROM accounting_core.bank_account_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_reference = %s
            """,
            (bank_account_reference,),
        ).fetchone()
        self.assertIsNone(existing)

    def _assert_artifact_hash_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        source_artifact_hash: str,
    ) -> None:
        """Exclude tenant/source-hash uniqueness as a parent-admission failure path."""
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

    def _statement_id_count(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        bank_statement_record_id: UUID,
    ) -> int:
        """Count a proposed globally unique statement-record UUID before admission."""
        return int(
            connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.bank_statement_record
                WHERE bank_statement_record_id = %s
                """,
                (bank_statement_record_id,),
            ).fetchone()[0]
        )

    def _fresh_hash(self) -> str:
        """Return a canonical SHA-256 value derived from fresh test entropy."""
        return "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()

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
