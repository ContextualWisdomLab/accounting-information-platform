"""REDs for fail-closed camt.053 related-remittance postal-address admission."""

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

_UNSUPPORTED_POSTAL_ERROR = (
    r"^related remittance postal address is not supported\. "
    r"Remove RltdRmtInf/RmtLctnDtls/PstlAdr or use a supported electronic address, "
    r"then retry ingest\.$"
)


class BankStatementDetailRelatedRemittancePostalAdmissionRedTests(unittest.TestCase):
    """Reject schema-admitted postal routing until its semantics are preserved completely."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one supported electronic location and one postal-address extension."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(fixture.count(marker), 1)

        electronic_location = (
            "            <RltdRmtInf>\n"
            "              <RmtId>RMT-LOC-POSTAL-GATE</RmtId>\n"
            "              <RmtLctnDtls>\n"
            "                <Mtd>EMAL</Mtd>\n"
            "                <ElctrncAdr>cash-application@example.test</ElctrncAdr>\n"
            "              </RmtLctnDtls>\n"
            "            </RltdRmtInf>\n"
        )
        postal_location = (
            "            <RltdRmtInf>\n"
            "              <RmtId>RMT-LOC-POSTAL-GATE</RmtId>\n"
            "              <RmtLctnDtls>\n"
            "                <Mtd>EMAL</Mtd>\n"
            "                <ElctrncAdr>cash-application@example.test</ElctrncAdr>\n"
            "                <PstlAdr>\n"
            "                  <Nm>Cash Application Desk</Nm>\n"
            "                  <Adr>\n"
            "                    <StrtNm>Sejong-daero</StrtNm>\n"
            "                    <BldgNb>110</BldgNb>\n"
            "                    <TwnNm>Seoul</TwnNm>\n"
            "                    <Ctry>KR</Ctry>\n"
            "                  </Adr>\n"
            "                </PstlAdr>\n"
            "              </RmtLctnDtls>\n"
            "            </RltdRmtInf>\n"
        )
        self.electronic_payload = fixture.replace(
            marker,
            electronic_location + marker,
            1,
        ).encode("utf-8")
        self.postal_payload = fixture.replace(
            marker,
            postal_location + marker,
            1,
        ).encode("utf-8")

        self.electronic_statement = parse_bank_statement_payload(
            self.electronic_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.electronic_statement.account_currency_code,
                "account_identifier_hash": self.electronic_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_direct_parser_rejects_unpreserved_related_remittance_postal_address(self) -> None:
        """Parser fails closed instead of silently dropping schema-admitted PstlAdr semantics."""
        with self.assertRaisesRegex(AccountingValidationError, _UNSUPPORTED_POSTAL_ERROR):
            parse_bank_statement_payload(
                self.postal_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_postal_address_before_it_can_alias_electronic_evidence(
        self,
    ) -> None:
        """A valid electronic base stays admissible while its postal extension fails closed."""
        accepted = accept_bank_statement_evidence(
            self._command(self.electronic_payload, "electronic"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _UNSUPPORTED_POSTAL_ERROR):
            accept_bank_statement_evidence(
                self._command(self.postal_payload, "postal"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-related-remittance-postal-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
