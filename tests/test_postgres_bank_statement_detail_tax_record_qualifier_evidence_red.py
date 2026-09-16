"""PostgreSQL REDs for omitted TaxRecord3 qualifier evidence."""

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
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting
from tests.test_postgres_bank_statement_detail_tax_evidence_red import (
    BankStatementDetailTaxEvidenceRedTests as TaxEvidenceRed,
)

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DETAIL_TAX_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/Tax"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailTaxRecordQualifierEvidenceRedTests(unittest.TestCase):
    """Retain optional TaxRecord3 qualifiers as bank-reported provenance."""

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

        self.records = (
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
            (
                "LOCAL_SURCHARGE",
                "LOCAL",
                "2026",
                "MM08",
                "1.00",
                "25000.00",
                "KRW",
                "250.00",
                "KRW",
            ),
        )
        self.tax = {
            "administration_zone": "KR-SEOUL",
            "reference_number": "TAX-REF-001",
            "method": "WITHHOLDING",
            "total_taxable_base_amount": "25000.00",
            "total_taxable_base_currency_code": "KRW",
            "total_tax_amount": "2750.00",
            "total_tax_currency_code": "KRW",
            "tax_date": "2026-08-24",
            "records": self.records,
        }
        self.base_qualifiers = {
            "category_details": "DOMESTIC_GOODS",
            "debtor_status": "RESIDENT",
            "certificate_identification": "CERT-2026-001",
            "forms_code": "FORM-VAT-01",
            "additional_information": "Bank-reported VAT settlement",
        }
        self.changed_qualifiers = {
            "category-details": dict(
                self.base_qualifiers, category_details="DOMESTIC_SERVICES"
            ),
            "debtor-status": dict(self.base_qualifiers, debtor_status="NONRESIDENT"),
            "certificate": dict(
                self.base_qualifiers, certificate_identification="CERT-2026-002"
            ),
            "forms-code": dict(self.base_qualifiers, forms_code="FORM-VAT-02"),
            "additional-information": dict(
                self.base_qualifiers,
                additional_information="Bank-reported VAT settlement revised",
            ),
        }

        tax_payload = TaxEvidenceRed._with_tax(fixture, marker, self.tax)
        self.base_payload = self._with_first_record_qualifiers(
            tax_payload, self.base_qualifiers
        )
        self.changed_payloads = {
            name: self._with_first_record_qualifiers(tax_payload, qualifiers)
            for name, qualifiers in self.changed_qualifiers.items()
        }
        self.reformatted_payload = self.base_payload.replace(
            b"</CtgyDtls>\n                <DbtrSts>",
            b"</CtgyDtls>\n\n                <DbtrSts>",
            1,
        )
        self.assertNotEqual(self.base_payload, self.reformatted_payload)

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.changed_payloads.items()
        }
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

    def test_tax_record_qualifiers_are_independently_material_evidence(self) -> None:
        base_hash = self._expected_hash(self.base_qualifiers)
        self.assertRegex(base_hash, _HASH_PATTERN)
        base_detail = self.base_statement.entries[0].entry_details[0]
        self.assertEqual(
            getattr(base_detail, "detail_tax_evidence_hash", None), base_hash
        )
        TaxEvidenceRed._assert_entry_hash_binding(
            self.base_statement.entries[0], base_hash
        )

        changed_hashes: list[str] = []
        for name, statement in self.changed_statements.items():
            with self.subTest(name=name):
                expected_hash = self._expected_hash(self.changed_qualifiers[name])
                detail = statement.entries[0].entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertNotEqual(expected_hash, base_hash)
                self.assertEqual(
                    getattr(detail, "detail_tax_evidence_hash", None), expected_hash
                )
                TaxEvidenceRed._assert_entry_hash_binding(
                    statement.entries[0], expected_hash
                )
                self.assertEqual(
                    statement.account_identifier_hash,
                    self.base_statement.account_identifier_hash,
                )
                self.assertEqual(
                    statement.entries[0].entry_amount,
                    self.base_statement.entries[0].entry_amount,
                )
                self.assertEqual(detail.detail_amount, base_detail.detail_amount)
                self.assertNotEqual(detail.source_detail_hash, base_detail.source_detail_hash)
                self.assertNotEqual(
                    statement.entries[0].source_entry_hash,
                    self.base_statement.entries[0].source_entry_hash,
                )
                self.assertNotEqual(
                    statement.normalized_payload_hash,
                    self.base_statement.normalized_payload_hash,
                )
                self.assertEqual(
                    statement.entries[1].source_entry_hash,
                    self.base_statement.entries[1].source_entry_hash,
                )
                changed_hashes.append(expected_hash)

        self.assertEqual(len(set(changed_hashes)), len(changed_hashes))

    def test_tax_record_qualifier_layout_is_not_semantic_identity(self) -> None:
        expected_hash = self._expected_hash(self.base_qualifiers)
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

    def test_changed_tax_record_qualifiers_require_explicit_correction(self) -> None:
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for name, payload in self.changed_payloads.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    AccountingValidationError, _CORRECTION_ERROR
                ):
                    accept_bank_statement_evidence(
                        self._command(payload, name),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_tax_record_qualifiers_without_changing_amount_truth(
        self,
    ) -> None:
        expected_hash = self._expected_hash(self.base_qualifiers)
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
        first_record = records[0]
        self.assertEqual(first_record.get("type"), "VAT")
        self.assertEqual(first_record.get("category"), "OUTPUT")
        for key, value in self.base_qualifiers.items():
            self.assertEqual(first_record.get(key), value)

        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _expected_hash(self, qualifiers: dict[str, str]) -> str:
        records = []
        for index, record in enumerate(self.records):
            projection = {
                "type": record[0],
                "category": record[1],
                "period_year": record[2],
                "period_type": record[3],
                "rate": record[4],
                "taxable_base_amount": record[5],
                "taxable_base_currency_code": record[6],
                "total_amount": record[7],
                "total_amount_currency_code": record[8],
            }
            if index == 0:
                projection.update(qualifiers)
            records.append(projection)

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
                "records": records,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_first_record_qualifiers(
        payload: bytes, qualifiers: dict[str, str]
    ) -> bytes:
        prefix = (
            b"              <Rcrd>\n"
            b"                <Tp>VAT</Tp>\n"
            b"                <Ctgy>OUTPUT</Ctgy>\n"
            b"                <Prd>\n"
        )
        replacement_prefix = (
            "              <Rcrd>\n"
            "                <Tp>VAT</Tp>\n"
            "                <Ctgy>OUTPUT</Ctgy>\n"
            f"                <CtgyDtls>{qualifiers['category_details']}</CtgyDtls>\n"
            f"                <DbtrSts>{qualifiers['debtor_status']}</DbtrSts>\n"
            f"                <CertId>{qualifiers['certificate_identification']}</CertId>\n"
            f"                <FrmsCd>{qualifiers['forms_code']}</FrmsCd>\n"
            "                <Prd>\n"
        ).encode("utf-8")
        if payload.count(prefix) != 1:
            raise AssertionError("fixture must contain one target VAT TaxRecord3")
        qualified = payload.replace(prefix, replacement_prefix, 1)

        suffix = (
            b"                </TaxAmt>\n"
            b"              </Rcrd>\n"
            b"              <Rcrd>\n"
            b"                <Tp>LOCAL_SURCHARGE</Tp>\n"
        )
        replacement_suffix = (
            "                </TaxAmt>\n"
            f"                <AddtlInf>{qualifiers['additional_information']}</AddtlInf>\n"
            "              </Rcrd>\n"
            "              <Rcrd>\n"
            "                <Tp>LOCAL_SURCHARGE</Tp>\n"
        ).encode("utf-8")
        if qualified.count(suffix) != 1:
            raise AssertionError("fixture must contain one target VAT TaxRecord3 close")
        return qualified.replace(suffix, replacement_suffix, 1)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-tax-record-qualifier-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
