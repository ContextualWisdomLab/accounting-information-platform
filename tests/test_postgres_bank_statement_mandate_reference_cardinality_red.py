"""PostgreSQL REDs for ambiguous camt.053 mandate-reference evidence."""

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


class BankStatementMandateReferenceCardinalityRedTests(unittest.TestCase):
    """Reject conflicting mandate references instead of selecting the first MndtId."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one registered account and one detail with contradictory mandate references."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.reference_marker = (
            "            <Refs>\n"
            "              <EndToEndId>E2E-1</EndToEndId>\n"
            "              <MndtId>MND-1</MndtId>\n"
            "            </Refs>"
        )
        self.assertEqual(self.fixture.count(self.reference_marker), 1)
        conflicting_marker = self.reference_marker.replace(
            "              <MndtId>MND-1</MndtId>\n",
            "              <MndtId>MND-1</MndtId>\n"
            "              <MndtId>MND-CONFLICT</MndtId>\n",
            1,
        )
        self.payload = self.fixture.replace(
            self.reference_marker,
            conflicting_marker,
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

    def test_adapter_rejects_conflicting_mandate_references(self) -> None:
        """Do not collapse contradictory TxDtls/Refs/MndtId evidence to the first value."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_conflicting_mandate_references(self) -> None:
        """Require parser rejection before accepting the supported-ingest rejection oracle."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.payload,
                CAMT053_MESSAGE_DEFINITION,
            )

        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": (
                        f"mandate-reference-cardinality-{uuid.uuid4().hex}"
                    ),
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )


if __name__ == "__main__":
    unittest.main()
