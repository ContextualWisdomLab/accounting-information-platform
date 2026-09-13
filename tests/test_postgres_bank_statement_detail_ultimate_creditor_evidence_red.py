"""PostgreSQL REDs for transaction-detail ultimate-creditor evidence."""

from __future__ import annotations

import hashlib
import json
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


class BankStatementDetailUltimateCreditorEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported ultimate-creditor evidence at transaction-detail scope."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create statements differing only in one ultimate-creditor name."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "              </Dbtr>\n"
            "            </RltdPties>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.first_ultimate_creditor_name = "Ultimate Creditor One"
        self.second_ultimate_creditor_name = "Ultimate Creditor Two"
        self.first_payload = self._with_ultimate_creditor(
            fixture,
            marker,
            self.first_ultimate_creditor_name,
        )
        self.second_payload = self._with_ultimate_creditor(
            fixture,
            marker,
            self.second_ultimate_creditor_name,
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

    def test_ultimate_creditor_change_changes_detail_entry_and_statement_hashes(self) -> None:
        """Changing only UltmtCdtr/Pty/Nm changes all retained evidence identities."""
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

    def test_same_statement_identity_requires_correction_for_changed_ultimate_creditor(self) -> None:
        """Changed ultimate-creditor evidence reaches the statement correction boundary."""
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

    def test_entry_lookup_preserves_ultimate_creditor_evidence_hash(self) -> None:
        """Buyer detail reads retain a digest without disclosing the reported party name."""
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
        expected_hash = "sha256:" + hashlib.sha256(
            self.first_ultimate_creditor_name.encode("utf-8")
        ).hexdigest()
        self._assert_digest_only_detail_projection(
            first_detail,
            "ultimate_creditor_evidence_hash",
            expected_hash,
            self.first_ultimate_creditor_name,
        )

    def _assert_digest_only_detail_projection(
        self,
        detail: dict[str, object],
        evidence_key: str,
        expected_hash: str,
        raw_name: str,
    ) -> None:
        """Allow ordinary detail facts plus irreversible evidence digests only."""
        plain_fields = {
            "detail_sequence_number",
            "source_locator_path",
            "detail_amount",
            "detail_currency_code",
            "credit_debit_code",
            "end_to_end_reference",
            "remittance_evidence_text",
            "source_detail_hash",
        }
        evidence_fields = {key for key in detail if key.endswith("_evidence_hash")}
        self.assertEqual(set(detail) - plain_fields - evidence_fields, set())
        for key in evidence_fields:
            value = detail[key]
            self.assertIsInstance(value, str)
            self.assertRegex(value, r"\Asha256:[0-9a-f]{64}\Z")
        self.assertEqual(detail[evidence_key], expected_hash)
        serialized_document = json.dumps(detail, sort_keys=True, default=str)
        self.assertNotIn(raw_name, serialized_document)

    @staticmethod
    def _with_ultimate_creditor(fixture: str, marker: str, value: str) -> bytes:
        """Insert one schema-shaped ultimate creditor after the existing debtor."""
        replacement = (
            "              </Dbtr>\n"
            "              <UltmtCdtr>\n"
            "                <Pty>\n"
            f"                  <Nm>{value}</Nm>\n"
            "                </Pty>\n"
            "              </UltmtCdtr>\n"
            "            </RltdPties>"
        )
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"ultimate-creditor-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
