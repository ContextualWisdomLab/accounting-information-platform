"""REDs for camt.053 related-remittance identifier cardinality."""

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


class BankStatementDetailRelatedRemittanceIdentifierCardinalityRedTests(unittest.TestCase):
    """Reject RemittanceLocation8 rows containing more than one optional RmtId."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one duplicate-RmtId payload while preserving valid routing children."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(fixture.count(marker), 1)

        duplicate_identifier = (
            "            <RltdRmtInf>\n"
            "              <RmtId>RMT-LOC-ID-001</RmtId>\n"
            "              <RmtId>RMT-LOC-ID-002</RmtId>\n"
            "              <RmtLctnDtls>\n"
            "                <Mtd>EMAL</Mtd>\n"
            "                <ElctrncAdr>cash-application@example.test</ElctrncAdr>\n"
            "              </RmtLctnDtls>\n"
            "            </RltdRmtInf>\n"
        )
        self.payload = fixture.replace(
            marker,
            duplicate_identifier + marker,
            1,
        ).encode("utf-8")

        canonical = parse_bank_statement_payload(
            fixture.encode("utf-8"), CAMT053_MESSAGE_DEFINITION
        )
        self.bank_account_reference = (
            f"urn:cwl:bank_account:related-remittance-id:{uuid.uuid4().hex}"
        )
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

    def test_parser_rejects_duplicate_related_remittance_identifier(self) -> None:
        """RmtId is optional but singleton and must not be first-match collapsed."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(self.payload, CAMT053_MESSAGE_DEFINITION)

    def test_supported_ingest_rejects_duplicate_related_remittance_identifier(self) -> None:
        """Contradictory remittance identity fails before immutable statement acceptance."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": (
                        f"detail-related-remittance-id-cardinality-{uuid.uuid4().hex}"
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
