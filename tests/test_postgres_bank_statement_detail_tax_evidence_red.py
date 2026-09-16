"""PostgreSQL REDs for camt.053 transaction-detail tax evidence."""

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

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DETAIL_TAX_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/Tax"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

TaxRecord = tuple[str, str, str, str, str, str, str, str, str]


class BankStatementDetailTaxEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported TaxData1 provenance without turning it into journal truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare tax variants while holding transaction accounting amount fixed."""
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

        self.base_records: tuple[TaxRecord, ...] = (
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
        self.base_tax = {
            "administration_zone": "KR-SEOUL",
            "reference_number": "TAX-REF-001",
            "method": "WITHHOLDING",
            "total_taxable_base_amount": "25000.00",
            "total_taxable_base_currency_code": "KRW",
            "total_tax_amount": "2750.00",
            "total_tax_currency_code": "KRW",
            "tax_date": "2026-08-24",
            "records": self.base_records,
        }
        self.changed_reference_tax = dict(self.base_tax, reference_number="TAX-REF-002")
        self.changed_total_tax = dict(self.base_tax, total_tax_amount="2750.01")
        changed_record_amount = list(self.base_records)
        changed_record_amount[1] = (
            "LOCAL_SURCHARGE",
            "LOCAL",
            "2026",
            "MM08",
            "1.00",
            "25000.00",
            "KRW",
            "250.01",
            "KRW",
        )
        self.changed_record_amount_tax = dict(
            self.base_tax,
            records=tuple(changed_record_amount),
        )
        self.changed_date_tax = dict(self.base_tax, tax_date="2026-08-25")
        self.reordered_tax = dict(self.base_tax, records=tuple(reversed(self.base_records)))

        self.base_payload = self._with_tax(fixture, marker, self.base_tax)
        self.changed_reference_payload = self._with_tax(
            fixture, marker, self.changed_reference_tax
        )
        self.changed_total_tax_payload = self._with_tax(
            fixture, marker, self.changed_total_tax
        )
        self.changed_record_amount_payload = self._with_tax(
            fixture, marker, self.changed_record_amount_tax
        )
        self.changed_date_payload = self._with_tax(fixture, marker, self.changed_date_tax)
        self.reordered_payload = self._with_tax(fixture, marker, self.reordered_tax)

        tax_xml = self._tax_xml(self.base_tax)
        self.assertEqual(self.base_payload.count(tax_xml.encode("utf-8")), 1)
        reformatted = tax_xml.replace(
            "            <Tax>\n              <AdmstnZone>KR-SEOUL</AdmstnZone>\n",
            "            <Tax>\n"
            "              <AdmstnZone>\n"
            "                KR-SEOUL\n"
            "              </AdmstnZone>\n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            tax_xml.encode("utf-8"),
            reformatted.encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_reference_statement = parse_bank_statement_payload(
            self.changed_reference_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_total_tax_statement = parse_bank_statement_payload(
            self.changed_total_tax_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_record_amount_statement = parse_bank_statement_payload(
            self.changed_record_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_date_statement = parse_bank_statement_payload(
            self.changed_date_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reordered_statement = parse_bank_statement_payload(
            self.reordered_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_detail_tax_semantics_are_material_evidence(self) -> None:
        """Tax reference, totals, date, record amount, and source order affect identity."""
        variants = (
            (self.base_statement, self.base_tax),
            (self.changed_reference_statement, self.changed_reference_tax),
            (self.changed_total_tax_statement, self.changed_total_tax),
            (self.changed_record_amount_statement, self.changed_record_amount_tax),
            (self.changed_date_statement, self.changed_date_tax),
            (self.reordered_statement, self.reordered_tax),
        )
        hashes: list[str] = []
        for statement, tax in variants:
            with self.subTest(reference=tax["reference_number"], date=tax["tax_date"]):
                expected_hash = self._expected_hash(tax)
                detail = statement.entries[0].entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(detail, "detail_tax_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(statement.entries[0], expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed in (
            self.changed_reference_statement,
            self.changed_total_tax_statement,
            self.changed_record_amount_statement,
            self.changed_date_statement,
            self.reordered_statement,
        ):
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_amount,
                changed.entries[0].entry_amount,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_details[0].detail_amount,
                changed.entries[0].entry_details[0].detail_amount,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].entry_details[0].source_detail_hash,
                changed.entries[0].entry_details[0].source_detail_hash,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].source_entry_hash,
                changed.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.base_statement.normalized_payload_hash,
                changed.normalized_payload_hash,
            )
            self.assertEqual(
                self.base_statement.entries[1].source_entry_hash,
                changed.entries[1].source_entry_hash,
            )

    def test_xml_layout_does_not_change_detail_tax_semantics(self) -> None:
        """Whitespace remains raw-artifact provenance rather than TaxData1 identity."""
        expected_hash = self._expected_hash(self.base_tax)
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
        self._assert_entry_hash_binding(self.base_statement.entries[0], expected_hash)
        self._assert_entry_hash_binding(self.reformatted_statement.entries[0], expected_hash)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_detail_tax_requires_explicit_statement_correction(self) -> None:
        """Changed tax evidence cannot silently replace an accepted statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, changed_payload in (
            ("reference", self.changed_reference_payload),
            ("total-tax", self.changed_total_tax_payload),
            ("record-amount", self.changed_record_amount_payload),
            ("tax-date", self.changed_date_payload),
            ("record-order", self.reordered_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed_payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_detail_tax_without_recomputing_amount_truth(self) -> None:
        """Buyer reads expose TaxData1 evidence while transaction amount stays distinct."""
        expected_hash = self._expected_hash(self.base_tax)
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
        self.assertEqual(
            detail.get("tax_evidence"),
            {
                "administration_zone": "KR-SEOUL",
                "reference_number": "TAX-REF-001",
                "method": "WITHHOLDING",
                "total_taxable_base_amount": "25000.00",
                "total_taxable_base_currency_code": "KRW",
                "total_tax_amount": "2750.00",
                "total_tax_currency_code": "KRW",
                "tax_date": "2026-08-24",
                "records": [
                    {
                        "type": "VAT",
                        "category": "OUTPUT",
                        "period_year": "2026",
                        "period_type": "MM08",
                        "rate": "10.00",
                        "taxable_base_amount": "25000.00",
                        "taxable_base_currency_code": "KRW",
                        "total_amount": "2500.00",
                        "total_amount_currency_code": "KRW",
                    },
                    {
                        "type": "LOCAL_SURCHARGE",
                        "category": "LOCAL",
                        "period_year": "2026",
                        "period_type": "MM08",
                        "rate": "1.00",
                        "taxable_base_amount": "25000.00",
                        "taxable_base_currency_code": "KRW",
                        "total_amount": "250.00",
                        "total_amount_currency_code": "KRW",
                    },
                ],
            },
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to the purpose digest, not parallel raw tax fields."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("detail_tax_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact detail_tax_evidence_hash"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "detail_tax_evidence_hash"
            )

    @staticmethod
    def _expected_hash(tax: dict[str, object]) -> str:
        """Digest selected TaxData1 semantics including repeatable records in source order."""
        records = tax["records"]
        if not isinstance(records, tuple):
            raise AssertionError("test tax records must be an immutable tuple")
        preimage = json.dumps(
            {
                "evidence_type": _DETAIL_TAX_PURPOSE,
                "administration_zone": tax["administration_zone"],
                "reference_number": tax["reference_number"],
                "method": tax["method"],
                "total_taxable_base_amount": tax["total_taxable_base_amount"],
                "total_taxable_base_currency_code": tax[
                    "total_taxable_base_currency_code"
                ],
                "total_tax_amount": tax["total_tax_amount"],
                "total_tax_currency_code": tax["total_tax_currency_code"],
                "tax_date": tax["tax_date"],
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
                    }
                    for record in records
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _tax_xml(tax: dict[str, object]) -> str:
        """Return schema-ordered TaxData1 with repeatable TaxRecord3 evidence."""
        records = tax["records"]
        if not isinstance(records, tuple):
            raise AssertionError("test tax records must be an immutable tuple")
        rendered_records: list[str] = []
        for record in records:
            rendered_records.append(
                "              <Rcrd>\n"
                f"                <Tp>{record[0]}</Tp>\n"
                f"                <Ctgy>{record[1]}</Ctgy>\n"
                "                <Prd>\n"
                f"                  <Yr>{record[2]}</Yr>\n"
                f"                  <Tp>{record[3]}</Tp>\n"
                "                </Prd>\n"
                "                <TaxAmt>\n"
                f"                  <Rate>{record[4]}</Rate>\n"
                f"                  <TaxblBaseAmt Ccy=\"{record[6]}\">{record[5]}</TaxblBaseAmt>\n"
                f"                  <TtlAmt Ccy=\"{record[8]}\">{record[7]}</TtlAmt>\n"
                "                </TaxAmt>\n"
                "              </Rcrd>\n"
            )
        return (
            "            <Tax>\n"
            f"              <AdmstnZone>{tax['administration_zone']}</AdmstnZone>\n"
            f"              <RefNb>{tax['reference_number']}</RefNb>\n"
            f"              <Mtd>{tax['method']}</Mtd>\n"
            f"              <TtlTaxblBaseAmt Ccy=\"{tax['total_taxable_base_currency_code']}\">{tax['total_taxable_base_amount']}</TtlTaxblBaseAmt>\n"
            f"              <TtlTaxAmt Ccy=\"{tax['total_tax_currency_code']}\">{tax['total_tax_amount']}</TtlTaxAmt>\n"
            f"              <Dt>{tax['tax_date']}</Dt>\n"
            + "".join(rendered_records)
            + "            </Tax>\n"
        )

    @classmethod
    def _with_tax(cls, fixture: str, marker: str, tax: dict[str, object]) -> bytes:
        """Insert TaxData1 after remittance and before later optional transaction fields."""
        return fixture.replace(
            marker,
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
            + cls._tax_xml(tax)
            + "          </TxDtls>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-tax-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
