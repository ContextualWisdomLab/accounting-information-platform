"""PostgreSQL REDs for unambiguous camt.053 account-identification choice."""

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
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementAccountIdentificationChoiceRedTests(unittest.TestCase):
    """Reject statement accounts that populate both alternatives of AccountIdentification4Choice."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one lawful account for a source-real ambiguous account identifier."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.account_identifier_marker = (
            "<Id>\n"
            "          <Othr>\n"
            "            <Id>acct-opaque-fixture-only</Id>\n"
            "          </Othr>\n"
            "        </Id>"
        )
        self.assertEqual(self.fixture.count(self.account_identifier_marker), 1)
        self.iban = "DE89370400440532013000"
        self.payload = self._ambiguous_account_identifier_payload()

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier_hash": "sha256:"
                + hashlib.sha256(self.iban.encode("utf-8")).hexdigest(),
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_adapter_rejects_account_identifier_with_both_iban_and_other(self) -> None:
        """Fail closed instead of choosing IBAN when the account identifier choice is ambiguous."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_ambiguous_account_identifier_choice(self) -> None:
        """Do not retain a statement whose account-identification evidence is ambiguous."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": f"account-id-choice-{uuid.uuid4().hex}",
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def _ambiguous_account_identifier_payload(self) -> bytes:
        """Populate both IBAN and Other alternatives under the statement account Id choice."""
        replacement = (
            "<Id>\n"
            f"          <IBAN>{self.iban}</IBAN>\n"
            "          <Othr>\n"
            "            <Id>acct-opaque-fixture-only</Id>\n"
            "          </Othr>\n"
            "        </Id>"
        )
        return self.fixture.replace(self.account_identifier_marker, replacement, 1).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
