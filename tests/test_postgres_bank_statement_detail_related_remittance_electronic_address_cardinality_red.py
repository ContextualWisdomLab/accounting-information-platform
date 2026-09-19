"""REDs for camt.053 related-remittance electronic-address cardinality."""

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


class BankStatementDetailRelatedRemittanceElectronicAddressCardinalityRedTests(
    unittest.TestCase
):
    """Reject RemittanceLocationData2 rows with more than one electronic address."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one schema-valid method with a duplicate optional electronic address."""
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
        if fixture.count(marker) != 1:
            raise AssertionError("canonical RmtInf marker must occur exactly once")

        duplicate_electronic_address = (
            "            <RltdRmtInf>\n"
            "              <RmtId>RMT-LOC-ELADR-DUPLICATE</RmtId>\n"
            "              <RmtLctnDtls>\n"
            "                <Mtd>EMAL</Mtd>\n"
            "                <ElctrncAdr>cash-application@example.test</ElctrncAdr>\n"
            "                <ElctrncAdr>cash-application-backup@example.test</ElctrncAdr>\n"
            "              </RmtLctnDtls>\n"
            "            </RltdRmtInf>\n"
        )
        self.payload = fixture.replace(
            marker,
            duplicate_electronic_address + marker,
            1,
        ).encode("utf-8")

        canonical = parse_bank_statement_payload(
            fixture.encode("utf-8"), CAMT053_MESSAGE_DEFINITION
        )
        self.bank_account_reference = (
            f"urn:cwl:bank_account:related-remittance-eladr-cardinality:{uuid.uuid4().hex}"
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

    def test_parser_rejects_duplicate_related_remittance_electronic_address(self) -> None:
        """ElctrncAdr is optional 0..1 and must not be silently first-match collapsed."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(self.payload, CAMT053_MESSAGE_DEFINITION)

    def test_supported_ingest_rejects_duplicate_related_remittance_electronic_address(
        self,
    ) -> None:
        """Invalid routing cardinality fails before immutable statement evidence is accepted."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": (
                        "detail-related-remittance-eladr-cardinality-"
                        f"{uuid.uuid4().hex}"
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
