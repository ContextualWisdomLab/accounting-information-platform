"""PostgreSQL REDs for transaction-detail initiating-party evidence."""

from __future__ import annotations

import hashlib
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


class BankStatementDetailInitiatingPartyEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported initiating-party evidence at transaction-detail scope."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create statements differing only in one initiating-party name."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.baseline_payload = fixture.encode("utf-8")
        marker = (
            "            <RltdPties>\n"
            "              <Dbtr>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.first_initiating_party_name = "Initiating Party One"
        self.second_initiating_party_name = "Initiating Party Two"
        self.first_payload = self._with_initiating_party(
            fixture,
            marker,
            self.first_initiating_party_name,
        )
        self.second_payload = self._with_initiating_party(
            fixture,
            marker,
            self.second_initiating_party_name,
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
        self._register_bank_account(self.bank_account_reference)
        self.store = MemoryArtifactStore()

    def test_initiating_party_change_changes_detail_entry_and_statement_hashes(self) -> None:
        """Changing only RltdPties/InitgPty/Pty/Nm changes all retained evidence identities."""
        first_entry = self.first_statement.entries[0]
        second_entry = self.second_statement.entries[0]
        first_detail = first_entry.entry_details[0]
        second_detail = second_entry.entry_details[0]

        self.assertEqual(
            first_entry.counterparty_evidence_hash,
            second_entry.counterparty_evidence_hash,
        )
        self.assertNotEqual(first_detail.source_detail_hash, second_detail.source_detail_hash)
        self.assertNotEqual(first_entry.source_entry_hash, second_entry.source_entry_hash)
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_requires_correction_for_changed_initiating_party(self) -> None:
        """Changed initiating-party evidence reaches the statement correction boundary."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            r"statement identity already exists with different entry evidence",
        ):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_entry_lookup_preserves_initiating_party_evidence_hash(self) -> None:
        """Buyer detail reads expose only the initiating-party digest delta."""
        first_detail = self._ingest_and_read_first_detail(
            self.first_payload,
            self.bank_account_reference,
            "lookup",
        )
        baseline_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self._register_bank_account(baseline_account_reference)
        baseline_detail = self._ingest_and_read_first_detail(
            self.baseline_payload,
            baseline_account_reference,
            "baseline",
        )
        expected_hash = "sha256:" + hashlib.sha256(
            self.first_initiating_party_name.encode("utf-8")
        ).hexdigest()
        self._assert_digest_only_projection_delta(
            first_detail,
            baseline_detail,
            "initiating_party_evidence_hash",
            expected_hash,
        )

    def _register_bank_account(self, bank_account_reference: str) -> None:
        """Register one test account for the fixture's immutable account evidence."""
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": bank_account_reference,
                "account_currency_code": self.first_statement.account_currency_code,
                "account_identifier_hash": self.first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def _ingest_and_read_first_detail(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one fixture on an isolated account and return its first detail projection."""
        accepted = accept_bank_statement_evidence(
            self._command(payload, suffix, bank_account_reference),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][0]["entry_details"][0]

    def _assert_digest_only_projection_delta(
        self,
        detail: dict[str, object],
        baseline_detail: dict[str, object],
        evidence_key: str,
        expected_hash: str,
    ) -> None:
        """Require the party-bearing projection to differ only by digest and detail identity."""
        self.assertEqual(detail[evidence_key], expected_hash)
        for projection in (detail, baseline_detail):
            source_detail_hash = projection["source_detail_hash"]
            self.assertIsInstance(source_detail_hash, str)
            self.assertRegex(source_detail_hash, r"\Asha256:[0-9a-f]{64}\Z")
        actual_projection = dict(detail)
        baseline_projection = dict(baseline_detail)
        actual_projection.pop(evidence_key)
        baseline_projection.pop(evidence_key, None)
        actual_projection.pop("source_detail_hash")
        baseline_projection.pop("source_detail_hash")
        self.assertEqual(actual_projection, baseline_projection)

    @staticmethod
    def _with_initiating_party(fixture: str, marker: str, value: str) -> bytes:
        """Insert one schema-shaped initiating party before the existing debtor."""
        replacement = (
            "            <RltdPties>\n"
            "              <InitgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{value}</Nm>\n"
            "                </Pty>\n"
            "              </InitgPty>\n"
            "              <Dbtr>"
        )
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    def _command(
        self,
        payload: bytes,
        suffix: str,
        bank_account_reference: str | None = None,
    ) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference or self.bank_account_reference,
            "ingestion_idempotency_key": f"initiating-party-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
