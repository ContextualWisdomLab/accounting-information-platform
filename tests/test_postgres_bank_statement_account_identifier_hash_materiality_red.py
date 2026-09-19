"""PostgreSQL RED for account identity in normalized statement evidence."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementAccountIdentifierHashMaterialityRedTests(unittest.TestCase):
    """Bind normalized statement identity to the exact reported bank account."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two source-real statements differing only in Acct/Id/Othr/Id."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "            <Id>acct-opaque-fixture-only</Id>"
        self.assertEqual(fixture.count(marker), 1)
        self.first_payload = fixture.encode("utf-8")
        self.second_payload = fixture.replace(
            marker,
            "            <Id>acct-opaque-fixture-second</Id>",
            1,
        ).encode("utf-8")
        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.first_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.second_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self._register(self.first_reference, self.first_statement.account_identifier_hash)
        self._register(self.second_reference, self.second_statement.account_identifier_hash)
        self.store = MemoryArtifactStore()

    def test_account_identifier_is_material_to_normalized_statement_hash(self) -> None:
        """Two reported accounts cannot alias to one normalized statement evidence identity."""
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_supported_ingest_retains_distinct_normalized_hashes_per_reported_account(self) -> None:
        """Persisted statement evidence remains account-specific after supported ingest."""
        first = accept_bank_statement_evidence(
            self._command(self.first_reference, self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        second = accept_bank_statement_evidence(
            self._command(self.second_reference, self.second_payload, "second"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertNotEqual(first["source_artifact_hash"], second["source_artifact_hash"])
        self.assertNotEqual(
            first["normalized_payload_hash"],
            second["normalized_payload_hash"],
        )

    def _register(self, bank_account_reference: str, account_identifier_hash: str) -> None:
        """Register one bank account with the exact source account identity hash."""
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier_hash": account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def _command(
        self,
        bank_account_reference: str,
        payload: bytes,
        suffix: str,
    ) -> dict[str, object]:
        """Return one supported ingest command for the exact reported account."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": f"account-hash-materiality-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
