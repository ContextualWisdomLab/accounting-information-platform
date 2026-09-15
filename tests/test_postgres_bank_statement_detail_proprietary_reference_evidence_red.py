"""PostgreSQL REDs for repeated proprietary transaction-reference evidence."""

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


class BankStatementDetailProprietaryReferenceEvidenceRedTests(unittest.TestCase):
    """Keep every present Refs/Prtry type/reference pair as immutable evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create two statements differing only in one repeated proprietary reference."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "              <MndtId>MND-1</MndtId>"
        self.assertEqual(fixture.count(marker), 1)
        self.first_reference = "ACQUIRER-REF-FIRST"
        self.second_reference = "ACQUIRER-REF-SECOND"
        self.stable_reference = "TERMINAL-REF-STABLE"
        first_proprietary = (
            "              <MndtId>MND-1</MndtId>\n"
            "              <Prtry>\n"
            "                <Tp>ACQUIRER_TRACE</Tp>\n"
            f"                <Ref>{self.first_reference}</Ref>\n"
            "              </Prtry>\n"
            "              <Prtry>\n"
            "                <Tp>TERMINAL_TRACE</Tp>\n"
            f"                <Ref>{self.stable_reference}</Ref>\n"
            "              </Prtry>"
        )
        second_proprietary = first_proprietary.replace(
            self.first_reference,
            self.second_reference,
            1,
        )
        self.first_payload = fixture.replace(marker, first_proprietary, 1).encode("utf-8")
        self.second_payload = fixture.replace(marker, second_proprietary, 1).encode("utf-8")
        self.assertEqual(self.first_payload.count(b"<Prtry>"), 2)
        self.assertEqual(self.second_payload.count(b"<Prtry>"), 2)
        self.assertEqual(
            self.second_payload,
            self.first_payload.replace(
                self.first_reference.encode("utf-8"),
                self.second_reference.encode("utf-8"),
                1,
            ),
        )
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

    def test_changed_repeated_proprietary_reference_changes_canonical_hashes(self) -> None:
        """Changing one Prtry/Ref changes detail, entry, and statement identity."""
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

    def test_same_statement_identity_cannot_replay_changed_proprietary_reference(self) -> None:
        """Changed proprietary source evidence requires correction, not silent replay."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            (
                r"^statement identity already exists with different entry evidence\. "
                r"Use an explicit correction contract, then retry ingest\.$"
            ),
        ):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_entry_lookup_preserves_all_present_proprietary_references(self) -> None:
        """Buyer reads retain every typed proprietary reference in source order."""
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
            first_detail["proprietary_transaction_references"],
            [
                {"type": "ACQUIRER_TRACE", "reference": self.first_reference},
                {"type": "TERMINAL_TRACE", "reference": self.stable_reference},
            ],
        )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-proprietary-reference-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
