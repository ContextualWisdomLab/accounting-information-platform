"""PostgreSQL RED for immutable bank-statement evidence population closure."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from uuid import UUID

import psycopg

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_assignment,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementPopulationClosureRedTests(unittest.TestCase):
    """Prevent accepted statement or entry evidence from gaining late child rows."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one accepted bank-statement hierarchy through supported commands."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        payload = load_canonical_statement_fixture()
        canonical = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        self.account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "account_currency_code": canonical.account_currency_code,
                "account_identifier_hash": canonical.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        accept_bank_account_assignment(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
                "assignment_idempotency_key": f"population-closure-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.document = accept_bank_statement_evidence(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "ingestion_idempotency_key": f"urn:cwl:bank_statement:{uuid.uuid4().hex}",
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": payload.decode("utf-8"),
                "source_artifact_hash": "sha256:" + hashlib.sha256(payload).hexdigest(),
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )

    def test_accepted_statement_cannot_gain_a_late_entry(self) -> None:
        """A new child entry cannot change a statement after its payload hash is retained."""
        statement_id = UUID(str(self.document["bank_statement_record_id"]))

        with self._tenant_connection() as connection:
            before = connection.execute(
                """
                SELECT COUNT(*), MAX(entry_sequence_number), normalized_payload_hash
                FROM accounting_integration.bank_statement_record AS statement
                LEFT JOIN accounting_integration.bank_statement_entry AS entry
                  ON entry.tenant_account_id = statement.tenant_account_id
                 AND entry.bank_statement_record_id = statement.bank_statement_record_id
                WHERE statement.tenant_account_id = accounting_core.current_tenant_account_id()
                  AND statement.bank_statement_record_id = %s
                GROUP BY statement.normalized_payload_hash
                """,
                (statement_id,),
            ).fetchone()
            self.assertIsNotNone(before)
            original_entry_count = int(before[0])
            next_sequence = int(before[1]) + 1
            original_payload_hash = str(before[2])
            self.assertGreater(original_entry_count, 0)
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1
                    FROM accounting_integration.bank_statement_entry
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_statement_record_id = %s
                      AND entry_sequence_number = %s
                    """,
                    (statement_id, next_sequence),
                ).fetchone()
            )

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
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
                            %s,
                            %s,
                            1.000000,
                            'KRW',
                            'CRDT',
                            %s
                        )
                        """,
                        (
                            statement_id,
                            next_sequence,
                            f"Document/BkToCstmrStmt/Stmt/Ntry[{next_sequence}]",
                            self._fresh_hash(),
                        ),
                    )

            after = connection.execute(
                """
                SELECT COUNT(*), statement.normalized_payload_hash
                FROM accounting_integration.bank_statement_record AS statement
                LEFT JOIN accounting_integration.bank_statement_entry AS entry
                  ON entry.tenant_account_id = statement.tenant_account_id
                 AND entry.bank_statement_record_id = statement.bank_statement_record_id
                WHERE statement.tenant_account_id = accounting_core.current_tenant_account_id()
                  AND statement.bank_statement_record_id = %s
                GROUP BY statement.normalized_payload_hash
                """,
                (statement_id,),
            ).fetchone()
            self.assertEqual(int(after[0]), original_entry_count)
            self.assertEqual(str(after[1]), original_payload_hash)

    def test_accepted_entry_cannot_gain_a_late_detail(self) -> None:
        """A new detail cannot change an entry after its source-entry hash is retained."""
        statement_id = UUID(str(self.document["bank_statement_record_id"]))

        with self._tenant_connection() as connection:
            entry = connection.execute(
                """
                SELECT bank_statement_entry_id, source_entry_hash,
                       entry_currency_code, credit_debit_code
                FROM accounting_integration.bank_statement_entry
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_statement_record_id = %s
                ORDER BY entry_sequence_number DESC
                LIMIT 1
                """,
                (statement_id,),
            ).fetchone()
            self.assertIsNotNone(entry)
            entry_id = entry[0]
            original_entry_hash = str(entry[1])
            entry_currency_code = str(entry[2])
            credit_debit_code = str(entry[3])
            detail_state = connection.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(detail_sequence_number), 0)
                FROM accounting_integration.bank_statement_entry_detail
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_statement_entry_id = %s
                """,
                (entry_id,),
            ).fetchone()
            original_detail_count = int(detail_state[0])
            next_sequence = int(detail_state[1]) + 1
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1
                    FROM accounting_integration.bank_statement_entry_detail
                    WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                      AND bank_statement_entry_id = %s
                      AND detail_sequence_number = %s
                    """,
                    (entry_id, next_sequence),
                ).fetchone()
            )

            with self.assertRaises(psycopg.IntegrityError):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_entry_detail (
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
                            accounting_core.current_tenant_account_id(),
                            %s,
                            %s,
                            %s,
                            1.000000,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            entry_id,
                            next_sequence,
                            (
                                "Document/BkToCstmrStmt/Stmt/Ntry[2]/"
                                f"NtryDtls/TxDtls[{next_sequence}]"
                            ),
                            entry_currency_code,
                            credit_debit_code,
                            self._fresh_hash(),
                        ),
                    )

            after = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_integration.bank_statement_entry_detail
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_statement_entry_id = %s
                """,
                (entry_id,),
            ).fetchone()
            retained_entry_hash = connection.execute(
                """
                SELECT source_entry_hash
                FROM accounting_integration.bank_statement_entry
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_statement_entry_id = %s
                """,
                (entry_id,),
            ).fetchone()[0]
            self.assertEqual(int(after[0]), original_detail_count)
            self.assertEqual(str(retained_entry_hash), original_entry_hash)

    def _fresh_hash(self) -> str:
        """Return a canonical SHA-256 value derived from fresh test entropy."""
        return "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()

    def _tenant_connection(self) -> psycopg.Connection[tuple[object, ...]]:
        """Open an admin session with the fixture tenant RLS context installed."""
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
