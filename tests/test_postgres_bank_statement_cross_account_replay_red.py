"""PostgreSQL RED for bank-statement replay bound to one registered bank account."""

from __future__ import annotations

import hashlib
import unittest
import uuid

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementCrossAccountReplayRedTests(unittest.TestCase):
    """Do not replay immutable statement evidence under a different bank-account Entity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one registered account whose source identity matches the canonical fixture."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.payload = load_canonical_statement_fixture()
        self.statement = parse_bank_statement_payload(
            self.payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.first_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.second_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.first_reference,
                "account_currency_code": self.statement.account_currency_code,
                "account_identifier_hash": self.statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def test_identical_source_artifact_cannot_replay_under_another_bank_account(self) -> None:
        """Artifact replay stays bound to the bank-account Entity that first accepted it."""
        first = accept_bank_statement_evidence(
            self._command(self.first_reference, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        self.assertEqual(first["bank_account_reference"], self.first_reference)

        try:
            accept_bank_account_record(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.second_reference,
                    "account_currency_code": self.statement.account_currency_code,
                    "account_identifier_hash": self.statement.account_identifier_hash,
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        except AccountingValidationError:
            self._assert_source_remains_bound(first)
            return

        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                self._command(self.second_reference, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=MemoryArtifactStore(),
            )

        self._assert_source_remains_bound(first)

    def _assert_source_remains_bound(self, first: dict[str, object]) -> None:
        """Prove the retained source artifact still names only the first accepted account."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
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
            rows = connection.execute(
                """
                SELECT account.bank_account_reference, statement.bank_statement_record_id
                FROM accounting_integration.bank_statement_record AS statement
                JOIN accounting_core.bank_account_record AS account
                  ON account.tenant_account_id = statement.tenant_account_id
                 AND account.bank_account_record_id = statement.bank_account_record_id
                WHERE statement.tenant_account_id = %s
                  AND statement.source_artifact_hash = %s
                """,
                (tenant_id, self.statement.source_artifact_hash),
            ).fetchall()
        self.assertEqual(
            rows,
            [(self.first_reference, uuid.UUID(str(first["bank_statement_record_id"])))],
        )

    def _command(self, bank_account_reference: str, suffix: str) -> dict[str, object]:
        """Return one supported ingest command for the canonical fixture."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": f"cross-account-replay-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": self.payload.decode("utf-8"),
            "source_artifact_hash": "sha256:" + hashlib.sha256(self.payload).hexdigest(),
        }


if __name__ == "__main__":
    unittest.main()
