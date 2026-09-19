"""REDs for camt.053 related-remittance location-method cardinality."""

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


class BankStatementDetailRelatedRemittanceMethodCardinalityRedTests(unittest.TestCase):
    """Reject RemittanceLocationData2 rows that do not contain exactly one method."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare missing- and duplicate-method payloads without changing accounting facts."""
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

        missing_method = (
            "            <RltdRmtInf>\n"
            "              <RmtId>RMT-LOC-METHOD-MISSING</RmtId>\n"
            "              <RmtLctnDtls>\n"
            "                <ElctrncAdr>cash-application@example.test</ElctrncAdr>\n"
            "              </RmtLctnDtls>\n"
            "            </RltdRmtInf>\n"
        )
        duplicate_method = (
            "            <RltdRmtInf>\n"
            "              <RmtId>RMT-LOC-METHOD-DUPLICATE</RmtId>\n"
            "              <RmtLctnDtls>\n"
            "                <Mtd>EMAL</Mtd>\n"
            "                <Mtd>URID</Mtd>\n"
            "                <ElctrncAdr>cash-application@example.test</ElctrncAdr>\n"
            "              </RmtLctnDtls>\n"
            "            </RltdRmtInf>\n"
        )
        self.payloads = {
            "missing-method": fixture.replace(marker, missing_method + marker, 1).encode(
                "utf-8"
            ),
            "duplicate-method": fixture.replace(marker, duplicate_method + marker, 1).encode(
                "utf-8"
            ),
        }

        canonical = parse_bank_statement_payload(
            fixture.encode("utf-8"), CAMT053_MESSAGE_DEFINITION
        )
        self.bank_account_reference = (
            f"urn:cwl:bank_account:related-remittance-method:{uuid.uuid4().hex}"
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

    def test_parser_rejects_missing_or_duplicate_related_remittance_method(self) -> None:
        """Mtd is exactly 1..1 and must not be silently absent or first-match collapsed."""
        for label, payload in self.payloads.items():
            with self.subTest(cardinality=label):
                with self.assertRaises(AccountingValidationError):
                    parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def test_supported_ingest_rejects_invalid_related_remittance_method_cardinality(self) -> None:
        """Invalid routing cardinality fails before immutable statement evidence is accepted."""
        for label, payload in self.payloads.items():
            with self.subTest(cardinality=label):
                with self.assertRaises(AccountingValidationError):
                    accept_bank_statement_evidence(
                        {
                            "tenant_reference": self.case.policy.tenant_reference,
                            "bank_account_reference": self.bank_account_reference,
                            "ingestion_idempotency_key": (
                                f"detail-related-remittance-method-{label}-{uuid.uuid4().hex}"
                            ),
                            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                            "statement_payload": payload.decode("utf-8"),
                        },
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )


if __name__ == "__main__":
    unittest.main()
