"""PostgreSQL REDs for per-detail mandate-reference evidence."""

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


class BankStatementDetailMandateReferenceEvidenceRedTests(unittest.TestCase):
    """Keep each present TxDtls mandate reference at transaction-detail granularity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create two multi-detail statements differing only in the second MndtId."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")

        first_detail_marker = (
            "              <EndToEndId>E2E-2A</EndToEndId>\n"
            "              <ChqNb>CHQ-9</ChqNb>"
        )
        second_detail_marker = "              <EndToEndId>E2E-2B</EndToEndId>"
        self.assertEqual(fixture.count(first_detail_marker), 1)
        self.assertEqual(fixture.count(second_detail_marker), 1)

        self.first_detail_mandate_reference = "MANDATE-REF-STABLE"
        self.first_second_detail_mandate_reference = "MANDATE-REF-SECOND-FIRST"
        self.second_second_detail_mandate_reference = "MANDATE-REF-SECOND-SECOND"

        with_first_detail_mandate = fixture.replace(
            first_detail_marker,
            "              <EndToEndId>E2E-2A</EndToEndId>\n"
            f"              <MndtId>{self.first_detail_mandate_reference}</MndtId>\n"
            "              <ChqNb>CHQ-9</ChqNb>",
            1,
        )
        self.first_payload = with_first_detail_mandate.replace(
            second_detail_marker,
            "              <EndToEndId>E2E-2B</EndToEndId>\n"
            f"              <MndtId>{self.first_second_detail_mandate_reference}</MndtId>",
            1,
        ).encode("utf-8")
        self.second_payload = with_first_detail_mandate.replace(
            second_detail_marker,
            "              <EndToEndId>E2E-2B</EndToEndId>\n"
            f"              <MndtId>{self.second_second_detail_mandate_reference}</MndtId>",
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

    def test_second_detail_mandate_reference_changes_canonical_hashes(self) -> None:
        """Changing only the second TxDtls MndtId changes detail, entry, and statement identity."""
        first_entry = self.first_statement.entries[1]
        second_entry = self.second_statement.entries[1]
        self.assertEqual(first_entry.mandate_reference, self.first_detail_mandate_reference)
        self.assertEqual(second_entry.mandate_reference, self.first_detail_mandate_reference)

        self.assertNotEqual(
            first_entry.entry_details[1].source_detail_hash,
            second_entry.entry_details[1].source_detail_hash,
        )
        self.assertNotEqual(first_entry.source_entry_hash, second_entry.source_entry_hash)
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_second_detail_mandate_reference(
        self,
    ) -> None:
        """Changed second-detail mandate evidence requires correction, not silent replay."""
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

    def test_entry_lookup_preserves_second_detail_mandate_reference(self) -> None:
        """Buyer-visible reads expose the mandate reference on its exact TxDtls detail."""
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

        second_detail = document["bank_statement_entries"][1]["entry_details"][1]
        self.assertIn("mandate_reference", second_detail)
        self.assertEqual(
            second_detail["mandate_reference"],
            self.first_second_detail_mandate_reference,
        )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-mandate-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
