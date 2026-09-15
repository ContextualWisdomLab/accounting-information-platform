"""PostgreSQL RED for server-owned bank-statement entry and detail provenance."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import unittest
import uuid
from uuid import UUID

import psycopg

from tests import test_postgres_posting as posting


class BankStatementEntryServerOwnedProvenanceRedTests(unittest.TestCase):
    """Keep immutable entry/detail identity and system time server-owned at admission."""

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

    def test_direct_insert_cannot_claim_database_owned_statement_entry_id(self) -> None:
        """A caller UUID cannot become immutable statement-entry identity evidence."""
        supplied_entry_id = uuid.uuid4()

        with self._tenant_connection() as connection:
            statement_id = self._create_lawful_statement(connection)
            self.assertEqual(self._entry_id_count(connection, supplied_entry_id), 0)
            self._assert_entry_sequence_absent(connection, statement_id, 1)

            try:
                with connection.transaction():
                    retained_entry_id = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_entry (
                            bank_statement_entry_id,
                            tenant_account_id,
                            bank_statement_record_id,
                            entry_sequence_number,
                            source_locator_path,
                            entry_amount,
                            entry_currency_code,
                            credit_debit_code,
                            source_entry_hash
                        )
                        VALUES (
                            %s,
                            accounting_core.current_tenant_account_id(),
                            %s,
                            1,
                            '/Document/BkToCstmrStmt/Stmt/Ntry[1]',
                            1.000000,
                            'KRW',
                            'CRDT',
                            %s
                        )
                        RETURNING bank_statement_entry_id
                        """,
                        (supplied_entry_id, statement_id, self._fresh_hash()),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_entry_id, supplied_entry_id)

    def test_direct_insert_cannot_claim_postgresql_owned_statement_entry_recorded_at(self) -> None:
        """A caller timestamp cannot become immutable statement-entry system-time evidence."""
        supplied_recorded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

        with self._tenant_connection() as connection:
            statement_id = self._create_lawful_statement(connection)
            self._assert_entry_sequence_absent(connection, statement_id, 1)

            try:
                with connection.transaction():
                    retained_recorded_at = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_entry (
                            tenant_account_id,
                            bank_statement_record_id,
                            entry_sequence_number,
                            source_locator_path,
                            entry_amount,
                            entry_currency_code,
                            credit_debit_code,
                            source_entry_hash,
                            recorded_at
                        )
                        VALUES (
                            accounting_core.current_tenant_account_id(),
                            %s,
                            1,
                            '/Document/BkToCstmrStmt/Stmt/Ntry[1]',
                            1.000000,
                            'KRW',
                            'CRDT',
                            %s,
                            %s
                        )
                        RETURNING recorded_at
                        """,
                        (statement_id, self._fresh_hash(), supplied_recorded_at),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_recorded_at, supplied_recorded_at)

    def test_direct_insert_cannot_claim_database_owned_entry_detail_id(self) -> None:
        """A caller UUID cannot become immutable entry-detail identity evidence."""
        supplied_detail_id = uuid.uuid4()

        with self._tenant_connection() as connection:
            entry_id = self._create_lawful_entry(connection)
            self.assertEqual(self._detail_id_count(connection, supplied_detail_id), 0)
            self._assert_detail_sequence_absent(connection, entry_id, 1)

            try:
                with connection.transaction():
                    retained_detail_id = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_entry_detail (
                            bank_statement_entry_detail_id,
                            tenant_account_id,
                            bank_statement_entry_id,
                            detail_sequence_number,
                            source_locator_path,
                            detail_amount,
                            detail_currency_code,
                            credit_debit_code,
                            source_detail_hash
                        )
                        VALUES (
                            %s,
                            accounting_core.current_tenant_account_id(),
                            %s,
                            1,
                            '/Document/BkToCstmrStmt/Stmt/Ntry[1]/NtryDtls[1]/TxDtls[1]',
                            1.000000,
                            'KRW',
                            'CRDT',
                            %s
                        )
                        RETURNING bank_statement_entry_detail_id
                        """,
                        (supplied_detail_id, entry_id, self._fresh_hash()),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_detail_id, supplied_detail_id)

    def test_direct_insert_cannot_claim_postgresql_owned_entry_detail_recorded_at(self) -> None:
        """A caller timestamp cannot become immutable entry-detail system-time evidence."""
        supplied_recorded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

        with self._tenant_connection() as connection:
            entry_id = self._create_lawful_entry(connection)
            self._assert_detail_sequence_absent(connection, entry_id, 1)

            try:
                with connection.transaction():
                    retained_recorded_at = connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_entry_detail (
                            tenant_account_id,
                            bank_statement_entry_id,
                            detail_sequence_number,
                            source_locator_path,
                            detail_amount,
                            detail_currency_code,
                            credit_debit_code,
                            source_detail_hash,
                            recorded_at
                        )
                        VALUES (
                            accounting_core.current_tenant_account_id(),
                            %s,
                            1,
                            '/Document/BkToCstmrStmt/Stmt/Ntry[1]/NtryDtls[1]/TxDtls[1]',
                            1.000000,
                            'KRW',
                            'CRDT',
                            %s,
                            %s
                        )
                        RETURNING recorded_at
                        """,
                        (entry_id, self._fresh_hash(), supplied_recorded_at),
                    ).fetchone()[0]
            except psycopg.IntegrityError:
                return

            self.assertNotEqual(retained_recorded_at, supplied_recorded_at)

    def _create_lawful_statement(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
    ) -> UUID:
        """Create a fresh statement hierarchy while omitting all server-owned provenance."""
        bank_account_reference = f"bank-account-{uuid.uuid4().hex}"
        source_artifact_hash = self._fresh_hash()
        statement_identity_reference = f"statement-{uuid.uuid4().hex}"
        ingestion_idempotency_key = f"ingest-{uuid.uuid4().hex}"

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
        return connection.execute(
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
            RETURNING bank_statement_record_id
            """,
            (
                bank_account_record_id,
                bank_statement_artifact_id,
                statement_identity_reference,
                source_artifact_hash,
                self._fresh_hash(),
                ingestion_idempotency_key,
            ),
        ).fetchone()[0]

    def _create_lawful_entry(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
    ) -> UUID:
        """Create one fresh immutable entry while leaving detail sequence 1 unused."""
        statement_id = self._create_lawful_statement(connection)
        return connection.execute(
            """
            INSERT INTO accounting_integration.bank_statement_entry (
                tenant_account_id,
                bank_statement_record_id,
                entry_sequence_number,
                source_locator_path,
                entry_amount,
                entry_currency_code,
                credit_debit_code,
                source_entry_hash
            )
            VALUES (
                accounting_core.current_tenant_account_id(),
                %s,
                1,
                '/Document/BkToCstmrStmt/Stmt/Ntry[1]',
                1.000000,
                'KRW',
                'CRDT',
                %s
            )
            RETURNING bank_statement_entry_id
            """,
            (statement_id, self._fresh_hash()),
        ).fetchone()[0]

    def _assert_entry_sequence_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        statement_id: UUID,
        sequence_number: int,
    ) -> None:
        """Exclude the entry sequence UNIQUE key as an incidental rejection path."""
        existing = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_entry
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_statement_record_id = %s
              AND entry_sequence_number = %s
            """,
            (statement_id, sequence_number),
        ).fetchone()
        self.assertIsNone(existing)

    def _assert_detail_sequence_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        entry_id: UUID,
        sequence_number: int,
    ) -> None:
        """Exclude the detail sequence UNIQUE key as an incidental rejection path."""
        existing = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_entry_detail
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_statement_entry_id = %s
              AND detail_sequence_number = %s
            """,
            (entry_id, sequence_number),
        ).fetchone()
        self.assertIsNone(existing)

    def _entry_id_count(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        bank_statement_entry_id: UUID,
    ) -> int:
        """Count a proposed globally unique statement-entry UUID before admission."""
        return int(
            connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.bank_statement_entry
                WHERE bank_statement_entry_id = %s
                """,
                (bank_statement_entry_id,),
            ).fetchone()[0]
        )

    def _detail_id_count(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        bank_statement_entry_detail_id: UUID,
    ) -> int:
        """Count a proposed globally unique entry-detail UUID before admission."""
        return int(
            connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.bank_statement_entry_detail
                WHERE bank_statement_entry_detail_id = %s
                """,
                (bank_statement_entry_detail_id,),
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
