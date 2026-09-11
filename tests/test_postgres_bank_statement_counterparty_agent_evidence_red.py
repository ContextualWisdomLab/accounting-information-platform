"""PostgreSQL REDs for camt.053 counterparty-agent evidence preservation."""

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
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementCounterpartyAgentEvidenceRedTests(unittest.TestCase):
    """Retain the reported debtor agent at transaction-detail evidence granularity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create statements differing only in the first detail's debtor-agent BICFI."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            </RltdPties>\n"
            "            <RmtInf>"
        )
        self.assertEqual(fixture.count(marker), 1)
        self.first_payload = self._with_debtor_agent(
            fixture,
            marker,
            "DEUTDEFF",
        )
        self.second_payload = self._with_debtor_agent(
            fixture,
            marker,
            "COBADEFF",
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

    def test_debtor_agent_change_changes_detail_entry_and_statement_hashes(self) -> None:
        """Changing only RltdAgts/DbtrAgt BICFI changes all retained evidence identities."""
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

    def test_same_statement_identity_cannot_replay_changed_debtor_agent(self) -> None:
        """Changed reported debtor agent requires correction, not silent replay."""
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

    @staticmethod
    def _with_debtor_agent(fixture: str, marker: str, bicfi: str) -> bytes:
        """Insert one reported debtor agent beside the existing related parties."""
        replacement = (
            "            </RltdPties>\n"
            "            <RltdAgts>\n"
            "              <DbtrAgt>\n"
            "                <FinInstnId>\n"
            f"                  <BICFI>{bicfi}</BICFI>\n"
            "                </FinInstnId>\n"
            "              </DbtrAgt>\n"
            "            </RltdAgts>\n"
            "            <RmtInf>"
        )
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"counterparty-agent-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
