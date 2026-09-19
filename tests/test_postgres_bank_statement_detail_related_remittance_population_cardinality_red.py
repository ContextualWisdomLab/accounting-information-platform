"""REDs for camt.053 related-remittance population cardinality."""

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


class BankStatementDetailRelatedRemittancePopulationCardinalityRedTests(
    unittest.TestCase
):
    """Preserve the V14 RltdRmtInf upper bound of ten records per detail."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare exactly-ten and eleven-record payloads with otherwise valid locations."""
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

        self.at_limit_payload = self._payload(fixture, marker, 10)
        self.overflow_payload = self._payload(fixture, marker, 11)

        canonical = parse_bank_statement_payload(
            fixture.encode("utf-8"), CAMT053_MESSAGE_DEFINITION
        )
        self.bank_account_reference = (
            f"urn:cwl:bank_account:related-remittance-population:{uuid.uuid4().hex}"
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

    def test_parser_accepts_ten_and_rejects_eleven_related_remittance_records(self) -> None:
        """RltdRmtInf is 0..10, so the upper-bound record is valid and the next is not."""
        statement = parse_bank_statement_payload(
            self.at_limit_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        detail = statement.entries[0].entry_details[0]
        records = getattr(detail, "related_remittance_information", None)
        if not isinstance(records, tuple):
            raise AssertionError("related remittance information must retain source population")
        self.assertEqual(len(records), 10)

        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.overflow_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_eleventh_related_remittance_record(self) -> None:
        """Population overflow fails before immutable statement evidence is accepted."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": (
                        "detail-related-remittance-population-overflow-"
                        f"{uuid.uuid4().hex}"
                    ),
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.overflow_payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    @classmethod
    def _payload(cls, fixture: str, marker: str, count: int) -> bytes:
        """Insert a requested number of schema-ordered RemittanceLocation8 records."""
        records = "".join(cls._record_xml(index) for index in range(1, count + 1))
        return fixture.replace(marker, records + marker, 1).encode("utf-8")

    @staticmethod
    def _record_xml(index: int) -> str:
        """Return one independently valid related-remittance record."""
        return (
            "            <RltdRmtInf>\n"
            f"              <RmtId>RMT-LOC-POP-{index:02d}</RmtId>\n"
            "              <RmtLctnDtls>\n"
            "                <Mtd>EMAL</Mtd>\n"
            f"                <ElctrncAdr>cash-app-{index:02d}@example.test</ElctrncAdr>\n"
            "              </RmtLctnDtls>\n"
            "            </RltdRmtInf>\n"
        )


if __name__ == "__main__":
    unittest.main()
