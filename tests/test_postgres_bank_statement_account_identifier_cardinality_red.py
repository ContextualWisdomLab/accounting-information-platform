"""PostgreSQL REDs for singleton camt.053 account-identifier cardinality."""

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


class BankStatementAccountIdentifierCardinalityRedTests(unittest.TestCase):
    """Reject an Acct/Id that supplies more than one proprietary identity choice."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one registered account and one ambiguous account-identity source."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "        <Id>\n"
            "          <Othr>\n"
            "            <Id>acct-opaque-fixture-only</Id>\n"
            "          </Othr>\n"
            "        </Id>\n"
            "        <Ccy>KRW</Ccy>"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)
        self.payload = self.fixture.replace(
            self.marker,
            "        <Id>\n"
            "          <Othr>\n"
            "            <Id>acct-opaque-fixture-only</Id>\n"
            "          </Othr>\n"
            "          <Othr>\n"
            "            <Id>acct-conflicting-fixture-only</Id>\n"
            "          </Othr>\n"
            "        </Id>\n"
            "        <Ccy>KRW</Ccy>",
            1,
        ).encode("utf-8")

        canonical = parse_bank_statement_payload(
            self.fixture.encode("utf-8"),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": canonical.account_currency_code,
                "account_identifier_hash": canonical.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_adapter_rejects_duplicate_proprietary_account_identity(self) -> None:
        """Do not silently choose the first Acct/Id/Othr when source identity is ambiguous."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_duplicate_proprietary_account_identity(self) -> None:
        """Do not admit ambiguous account identity merely because the first choice is registered."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": f"account-identity-cardinality-{uuid.uuid4().hex}",
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )


if __name__ == "__main__":
    unittest.main()
