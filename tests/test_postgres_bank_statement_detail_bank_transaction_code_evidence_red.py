"""PostgreSQL REDs for transaction-detail bank-transaction-code evidence."""

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


class BankStatementDetailBankTransactionCodeEvidenceRedTests(unittest.TestCase):
    """Keep TxDtls/BkTxCd distinct from entry-level transaction-code evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create two statements differing only in one detail proprietary code."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            </AmtDtls>\n"
            "            <RltdPties>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.first_code = "DETAIL-CODE-FIRST"
        self.second_code = "DETAIL-CODE-SECOND"
        self.issuer = "Fixture Bank"
        first_transaction_code = (
            "            </AmtDtls>\n"
            "            <BkTxCd>\n"
            "              <Prtry>\n"
            f"                <Cd>{self.first_code}</Cd>\n"
            f"                <Issr>{self.issuer}</Issr>\n"
            "              </Prtry>\n"
            "            </BkTxCd>\n"
            "            <RltdPties>"
        )
        self.first_payload = fixture.replace(marker, first_transaction_code, 1).encode("utf-8")
        self.second_payload = self.first_payload.replace(
            self.first_code.encode("utf-8"),
            self.second_code.encode("utf-8"),
            1,
        )
        self.assertEqual(self.first_payload.count(self.first_code.encode("utf-8")), 1)
        self.assertEqual(self.second_payload.count(self.second_code.encode("utf-8")), 1)
        self.assertEqual(
            self.second_payload,
            self.first_payload.replace(
                self.first_code.encode("utf-8"),
                self.second_code.encode("utf-8"),
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
        self.assertEqual(self.first_statement.entries[0].bank_transaction_domain_code, "PMNT")
        self.assertEqual(self.second_statement.entries[0].bank_transaction_domain_code, "PMNT")

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

    def test_changed_detail_transaction_code_changes_canonical_hashes(self) -> None:
        """Changing only TxDtls/BkTxCd changes detail, entry, and statement identity."""
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

    def test_same_statement_identity_cannot_replay_changed_detail_transaction_code(self) -> None:
        """Changed detail transaction-code evidence requires correction, not replay."""
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

    def test_entry_lookup_preserves_detail_transaction_code(self) -> None:
        """Buyer detail reads retain the transaction-level code and issuer."""
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
        self.assertEqual(first_detail["bank_transaction_proprietary_code"], self.first_code)
        self.assertEqual(first_detail["bank_transaction_proprietary_issuer"], self.issuer)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-bank-transaction-code-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
