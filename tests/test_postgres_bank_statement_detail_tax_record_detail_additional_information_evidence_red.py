"""PostgreSQL REDs for omitted TaxRecordDetails3 additional-information evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests.test_postgres_bank_statement_detail_tax_evidence_red import (
    BankStatementDetailTaxEvidenceRedTests as TaxEvidenceRed,
)
from tests.test_postgres_bank_statement_detail_tax_record_details_evidence_red import (
    BankStatementDetailTaxRecordDetailsEvidenceRedTests as TaxRecordDetailsRed,
)

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DETAIL_TAX_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/Tax"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

TaxAmountDetail = tuple[str, str, str, str, str, str]


class BankStatementDetailTaxRecordDetailAdditionalInformationEvidenceRedTests(
    unittest.TestCase
):
    """Retain TaxRecordDetails3/AddtlInf as bank-reported tax provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
            "          </TxDtls>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.tax = {
            "administration_zone": "KR-SEOUL",
            "reference_number": "TAX-REF-001",
            "method": "WITHHOLDING",
            "total_taxable_base_amount": "25000.00",
            "total_taxable_base_currency_code": "KRW",
            "total_tax_amount": "2500.00",
            "total_tax_currency_code": "KRW",
            "tax_date": "2026-08-24",
            "records": (
                (
                    "VAT",
                    "OUTPUT",
                    "2026",
                    "MM08",
                    "10.00",
                    "25000.00",
                    "KRW",
                    "2500.00",
                    "KRW",
                ),
            ),
        }
        self.details: tuple[TaxAmountDetail, ...] = (
            ("2026", "MM07", "2026-07-01", "2026-07-31", "1200.00", "KRW"),
            ("2026", "MM08", "2026-08-01", "2026-08-31", "1300.00", "KRW"),
        )
        self.base_additional_information = "Bank-reported July VAT split"
        self.changed_additional_information = "Bank-reported July VAT split revised"

        without_details = TaxEvidenceRed._with_tax(fixture, marker, self.tax)
        tax_amount_anchor = (
            b"                <TaxAmt>\n"
            b"                  <Rate>10.00</Rate>\n"
            b"                  <TaxblBaseAmt Ccy=\"KRW\">25000.00</TaxblBaseAmt>\n"
            b"                  <TtlAmt Ccy=\"KRW\">2500.00</TtlAmt>\n"
            b"                </TaxAmt>\n"
        )
        self.assertEqual(without_details.count(tax_amount_anchor), 1)
        details_payload = TaxRecordDetailsRed._with_tax_amount_details(
            without_details,
            tax_amount_anchor,
            self.details,
        )

        first_detail = TaxRecordDetailsRed._tax_amount_detail_xml(self.details[0])
        self.assertEqual(details_payload.count(first_detail), 1)
        self.base_payload = details_payload.replace(
            first_detail,
            self._detail_with_additional_information(
                first_detail, self.base_additional_information
            ),
            1,
        )
        self.changed_payload = details_payload.replace(
            first_detail,
            self._detail_with_additional_information(
                first_detail, self.changed_additional_information
            ),
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            b"                    <AddtlInf>Bank-reported July VAT split</AddtlInf>\n",
            b"\n                    <AddtlInf>Bank-reported July VAT split</AddtlInf>\n",
            1,
        )
        self.assertNotEqual(self.base_payload, self.reformatted_payload)

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.base_statement.account_currency_code,
                "account_identifier_hash": self.base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_tax_record_detail_additional_information_is_material_evidence(self) -> None:
        base_hash = self._expected_hash(self.base_additional_information)
        changed_hash = self._expected_hash(self.changed_additional_information)
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertRegex(changed_hash, _HASH_PATTERN)
        self.assertNotEqual(base_hash, changed_hash)

        base_detail = self.base_statement.entries[0].entry_details[0]
        changed_detail = self.changed_statement.entries[0].entry_details[0]
        self.assertEqual(
            getattr(base_detail, "detail_tax_evidence_hash", None), base_hash
        )
        self.assertEqual(
            getattr(changed_detail, "detail_tax_evidence_hash", None), changed_hash
        )
        TaxEvidenceRed._assert_entry_hash_binding(
            self.base_statement.entries[0], base_hash
        )
        TaxEvidenceRed._assert_entry_hash_binding(
            self.changed_statement.entries[0], changed_hash
        )

        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_statement.entries[0].entry_amount,
            self.changed_statement.entries[0].entry_amount,
        )
        self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.changed_statement.entries[0].source_entry_hash,
        )
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        )

    def test_tax_record_detail_additional_information_layout_is_not_identity(self) -> None:
        expected_hash = self._expected_hash(self.base_additional_information)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "detail_tax_evidence_hash", None), expected_hash
        )
        self.assertEqual(
            getattr(reformatted_detail, "detail_tax_evidence_hash", None), expected_hash
        )
        TaxEvidenceRed._assert_entry_hash_binding(
            self.reformatted_statement.entries[0], expected_hash
        )
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_tax_record_detail_additional_information_requires_correction(
        self,
    ) -> None:
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_tax_record_detail_additional_information(self) -> None:
        expected_hash = self._expected_hash(self.base_additional_information)
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]
        self.assertEqual(detail.get("detail_tax_evidence_hash"), expected_hash)

        tax_evidence = detail.get("tax_evidence")
        self.assertIsInstance(tax_evidence, dict)
        if not isinstance(tax_evidence, dict):
            raise AssertionError("tax_evidence must be a mapping")
        records = tax_evidence.get("records")
        self.assertIsInstance(records, list)
        if not isinstance(records, list) or not records:
            raise AssertionError("tax_evidence.records must preserve TaxRecord3")
        details = records[0].get("details")
        self.assertIsInstance(details, list)
        if not isinstance(details, list) or len(details) != 2:
            raise AssertionError("TaxRecord3 details must remain repeatable")
        self.assertEqual(
            details[0].get("additional_information"),
            self.base_additional_information,
        )
        self.assertNotIn("additional_information", details[1])

        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _expected_hash(self, additional_information: str) -> str:
        record = self.tax["records"][0]
        detail_projections = []
        for index, item in enumerate(self.details):
            projection = {
                "period_year": item[0],
                "period_type": item[1],
                "period_from_date": item[2],
                "period_to_date": item[3],
                "amount": item[4],
                "currency_code": item[5],
            }
            if index == 0:
                projection["additional_information"] = additional_information
            detail_projections.append(projection)

        preimage = json.dumps(
            {
                "evidence_type": _DETAIL_TAX_PURPOSE,
                "administration_zone": self.tax["administration_zone"],
                "reference_number": self.tax["reference_number"],
                "method": self.tax["method"],
                "total_taxable_base_amount": self.tax[
                    "total_taxable_base_amount"
                ],
                "total_taxable_base_currency_code": self.tax[
                    "total_taxable_base_currency_code"
                ],
                "total_tax_amount": self.tax["total_tax_amount"],
                "total_tax_currency_code": self.tax["total_tax_currency_code"],
                "tax_date": self.tax["tax_date"],
                "records": [
                    {
                        "type": record[0],
                        "category": record[1],
                        "period_year": record[2],
                        "period_type": record[3],
                        "rate": record[4],
                        "taxable_base_amount": record[5],
                        "taxable_base_currency_code": record[6],
                        "total_amount": record[7],
                        "total_amount_currency_code": record[8],
                        "details": detail_projections,
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _detail_with_additional_information(detail_xml: bytes, value: str) -> bytes:
        close = b"                  </Dtls>\n"
        if detail_xml.count(close) != 1:
            raise AssertionError("fixture must contain one TaxRecordDetails3 close")
        additional = f"                    <AddtlInf>{value}</AddtlInf>\n".encode(
            "utf-8"
        )
        return detail_xml.replace(close, additional + close, 1)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-tax-record-detail-additional-information-{suffix}-"
                f"{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
