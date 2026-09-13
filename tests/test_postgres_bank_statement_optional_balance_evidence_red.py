"""PostgreSQL REDs for retained optional camt.053 balance evidence."""

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


class BankStatementOptionalBalanceEvidenceRedTests(unittest.TestCase):
    """Do not discard a present CLAV balance from immutable statement identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create source-real statements that differ only in one CLAV amount."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.entry_marker = "      <Ntry>\n        <NtryRef>NTRY-1</NtryRef>"
        self.assertEqual(self.fixture.count(self.entry_marker), 1)

        self.first_payload = self._with_closing_available_balance("114000.00")
        self.second_payload = self._with_closing_available_balance("113000.00")
        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_statement = parse_bank_statement_payload(
            self.fixture.encode("utf-8"),
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

    def test_present_closing_available_balance_changes_normalized_identity(self) -> None:
        """Adding a source CLAV balance changes retained statement evidence identity."""
        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.first_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.first_statement.normalized_payload_hash,
        )

    def test_closing_available_amount_changes_normalized_identity(self) -> None:
        """Changing only CLAV/Amt changes the normalized statement evidence hash."""
        self.assertNotEqual(self.first_payload, self.second_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_closing_available_balance(self) -> None:
        """Changed CLAV evidence requires correction instead of replaying an earlier artifact."""
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

    def _with_closing_available_balance(self, amount: str) -> bytes:
        """Insert one bilateral closing-available balance before the first entry."""
        balance = (
            "      <Bal>\n"
            "        <Tp>\n"
            "          <CdOrPrtry>\n"
            "            <Cd>CLAV</Cd>\n"
            "          </CdOrPrtry>\n"
            "        </Tp>\n"
            f"        <Amt Ccy=\"KRW\">{amount}</Amt>\n"
            "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "        <Dt>\n"
            "          <Dt>2026-08-24</Dt>\n"
            "        </Dt>\n"
            "      </Bal>\n"
        )
        return self.fixture.replace(
            self.entry_marker,
            balance + self.entry_marker,
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh idempotency key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"optional-balance-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
