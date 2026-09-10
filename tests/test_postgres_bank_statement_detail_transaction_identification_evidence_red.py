"""PostgreSQL REDs for transaction-detail TransactionIdentification evidence."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementDetailTransactionIdentificationEvidenceRedTests(unittest.TestCase):
    """Keep present TxId references in normalized and persisted statement evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create two source-real statements differing only in one detail TxId."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "<EndToEndId>E2E-1</EndToEndId>\n"
            "              <MndtId>MND-1</MndtId>"
        )
        self.assertEqual(fixture.count(marker), 1)
        self.first_transaction_identification = "TRANSACTION-ID-FIRST"
        self.second_transaction_identification = "TRANSACTION-ID-SECOND"
        self.first_payload = fixture.replace(
            marker,
            "<EndToEndId>E2E-1</EndToEndId>\n"
            f"              <TxId>{self.first_transaction_identification}</TxId>\n"
            "              <MndtId>MND-1</MndtId>",
            1,
        ).encode("utf-8")
        self.second_payload = fixture.replace(
            marker,
            "<EndToEndId>E2E-1</EndToEndId>\n"
            f"              <TxId>{self.second_transaction_identification}</TxId>\n"
            "              <MndtId>MND-1</MndtId>",
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
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.first_statement.account_currency_code,
                "account_identifier_hash": self.first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_present_detail_transaction_identification_changes_canonical_hashes(self) -> None:
        """Changing only TxId changes detail, entry, and statement evidence identity."""
        first_detail = self.first_statement.entries[0].entry_details[0]
        second_detail = self.second_statement.entries[0].entry_details[0]

        self.assertNotEqual(first_detail.source_detail_hash, second_detail.source_detail_hash)
        self.assertNotEqual(
            self.first_statement.entries[0].source_entry_hash,
            self.second_statement.entries[0].source_entry_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_detail_transaction_identification(
        self,
    ) -> None:
        """Changed present TxId evidence requires correction, not silent identity replay."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_entry_lookup_preserves_present_detail_transaction_identification(self) -> None:
        """Buyer-visible detail reads expose a present source transaction identifier."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )

        first_detail = document["bank_statement_entries"][0]["entry_details"][0]
        self.assertEqual(
            first_detail["transaction_identification_reference"],
            self.first_transaction_identification,
        )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-txid-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
