"""PostgreSQL REDs for camt.053 creditor-account evidence preservation."""

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


class BankStatementCreditorAccountEvidenceRedTests(unittest.TestCase):
    """Retain a reported creditor account at transaction-detail evidence granularity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create outgoing-payment details differing only in the creditor IBAN."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RmtInf>"
        )
        self.assertEqual(fixture.count(marker), 1)
        self.first_creditor_account_iban = "FR7630006000011234567890189"
        self.second_creditor_account_iban = "FR1420041010050500013M02606"
        self.first_payload = self._with_creditor_account(
            fixture,
            marker,
            self.first_creditor_account_iban,
        )
        self.second_payload = self._with_creditor_account(
            fixture,
            marker,
            self.second_creditor_account_iban,
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

    def test_creditor_account_change_changes_detail_entry_and_statement_hashes(self) -> None:
        """Changing only CdtrAcct/Id/IBAN changes detail, entry, and statement identity."""
        first_detail = self.first_statement.entries[1].entry_details[0]
        second_detail = self.second_statement.entries[1].entry_details[0]

        self.assertNotEqual(first_detail.source_detail_hash, second_detail.source_detail_hash)
        self.assertNotEqual(
            self.first_statement.entries[1].source_entry_hash,
            self.second_statement.entries[1].source_entry_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_creditor_account(self) -> None:
        """Changed reported creditor account reaches the statement correction boundary."""
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

    def test_entry_lookup_preserves_creditor_account_evidence_hash(self) -> None:
        """Buyer-visible detail reads retain purpose-bound creditor-account evidence."""
        detail = self._ingest_and_read_target_detail(
            self.first_payload,
            self.bank_account_reference,
            "lookup",
        )
        self.assertEqual(
            detail["creditor_account_evidence_hash"],
            self._creditor_account_evidence_hash(),
        )

    def test_creditor_account_projection_differs_from_baseline_only_by_digest(self) -> None:
        """Creditor-account source evidence adds no reversible buyer projection field."""
        private_detail = self._ingest_and_read_target_detail(
            self.first_payload,
            self.bank_account_reference,
            "private-projection",
        )
        baseline_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": baseline_account_reference,
                "account_currency_code": self.first_statement.account_currency_code,
                "account_identifier_hash": self.first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        baseline_detail = self._ingest_and_read_target_detail(
            load_canonical_statement_fixture(),
            baseline_account_reference,
            "baseline",
        )

        expected_hash = self._creditor_account_evidence_hash()
        self.assertEqual(private_detail["creditor_account_evidence_hash"], expected_hash)
        for projection in (private_detail, baseline_detail):
            source_detail_hash = projection["source_detail_hash"]
            self.assertIsInstance(source_detail_hash, str)
            self.assertRegex(source_detail_hash, r"\Asha256:[0-9a-f]{64}\Z")
        actual_projection = dict(private_detail)
        baseline_projection = dict(baseline_detail)
        actual_projection.pop("creditor_account_evidence_hash")
        baseline_projection.pop("creditor_account_evidence_hash", None)
        actual_projection.pop("source_detail_hash")
        baseline_projection.pop("source_detail_hash")
        self.assertEqual(actual_projection, baseline_projection)

    @staticmethod
    def _with_creditor_account(fixture: str, marker: str, iban: str) -> bytes:
        """Insert one payee account into the first detail of the debit entry."""
        replacement = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RltdPties>\n"
            "              <CdtrAcct>\n"
            "                <Id>\n"
            f"                  <IBAN>{iban}</IBAN>\n"
            "                </Id>\n"
            "              </CdtrAcct>\n"
            "            </RltdPties>\n"
            "            <RmtInf>"
        )
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    def _ingest_and_read_target_detail(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one fixture and return the first detail of its debit entry."""
        accepted = accept_bank_statement_evidence(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": bank_account_reference,
                "ingestion_idempotency_key": (
                    f"creditor-account-{suffix}-{uuid.uuid4().hex}"
                ),
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": payload.decode("utf-8"),
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][1]["entry_details"][0]

    def _creditor_account_evidence_hash(self) -> str:
        """Return the purpose-bound digest expected by the buyer projection."""
        return "sha256:" + hashlib.sha256(
            self.first_creditor_account_iban.encode("utf-8")
        ).hexdigest()

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"creditor-account-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
