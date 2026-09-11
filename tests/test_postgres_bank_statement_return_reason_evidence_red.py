"""PostgreSQL REDs for camt.053 return-reason evidence preservation."""

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


class BankStatementReturnReasonEvidenceRedTests(unittest.TestCase):
    """Retain a bank-reported return reason at transaction-detail granularity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create reversal statements differing only in one return-reason code."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        reversal_marker = "        <RvslInd>false</RvslInd>"
        self.assertEqual(fixture.count(reversal_marker), 1)
        fixture = fixture.replace(
            reversal_marker,
            "        <RvslInd>true</RvslInd>",
            1,
        )
        detail_marker = (
            "            </RltdPties>\n"
            "            <RmtInf>"
        )
        self.assertEqual(fixture.count(detail_marker), 1)
        self.first_payload = self._with_return_reason(
            fixture,
            detail_marker,
            "AM09",
        )
        self.second_payload = self._with_return_reason(
            fixture,
            detail_marker,
            "AC04",
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

    def test_return_reason_change_changes_detail_entry_and_statement_hashes(self) -> None:
        """Changing only RtrInf/Rsn/Cd changes all retained evidence identities."""
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

    def test_same_statement_identity_cannot_replay_changed_return_reason(self) -> None:
        """Changed return evidence requires correction, not silent statement replay."""
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

    def test_entry_lookup_exposes_retained_return_reason(self) -> None:
        """Buyer-visible entry reads expose the bank-reported return reason code."""
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
        self.assertEqual(first_detail["return_reason_code"], "AM09")

    @staticmethod
    def _with_return_reason(fixture: str, marker: str, reason_code: str) -> bytes:
        """Insert one return reason beside the existing first-detail related parties."""
        replacement = (
            "            </RltdPties>\n"
            "            <RtrInf>\n"
            "              <Rsn>\n"
            f"                <Cd>{reason_code}</Cd>\n"
            "              </Rsn>\n"
            "            </RtrInf>\n"
            "            <RmtInf>"
        )
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"return-reason-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
