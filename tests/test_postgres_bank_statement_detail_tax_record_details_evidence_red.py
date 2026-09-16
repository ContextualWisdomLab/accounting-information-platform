"""PostgreSQL REDs for camt.053 TaxRecord3 tax-amount detail evidence."""

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

TaxAmountDetail = tuple[str, str, str, str, str, str]


class BankStatementDetailTaxRecordDetailsEvidenceRedTests(unittest.TestCase):
    """Retain TaxRecord3/TaxAmt/Dtls provenance without turning it into journal truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build tax-detail variants while holding every accounting amount fixed."""
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
        without_details = TaxEvidenceRed._with_tax(fixture, marker, self.tax)
        tax_amount_anchor = (
            b"                <TaxAmt>\n"
            b"                  <Rate>10.00</Rate>\n"
            b"                  <TaxblBaseAmt Ccy=\"KRW\">25000.00</TaxblBaseAmt>\n"
            b"                  <TtlAmt Ccy=\"KRW\">2500.00</TtlAmt>\n"
            b"                </TaxAmt>\n"
        )
        self.assertEqual(without_details.count(tax_amount_anchor), 1)

        self.base_details: tuple[TaxAmountDetail, ...] = (
            ("2026", "MM07", "2026-07-01", "2026-07-31", "1200.00", "KRW"),
            ("2026", "MM08", "2026-08-01", "2026-08-31", "1300.00", "KRW"),
        )
        changed_amount = list(self.base_details)
        changed_amount[0] = (
            "2026",
            "MM07",
            "2026-07-01",
            "2026-07-31",
            "1200.01",
            "KRW",
        )
        self.changed_amount_details = tuple(changed_amount)

        changed_period_start = list(self.base_details)
        changed_period_start[0] = (
            "2026",
            "MM07",
            "2026-07-02",
            "2026-07-31",
            "1200.00",
            "KRW",
        )
        self.changed_period_start_details = tuple(changed_period_start)
        self.reordered_details = tuple(reversed(self.base_details))

        self.base_payload = self._with_tax_amount_details(
            without_details, tax_amount_anchor, self.base_details
        )
        self.changed_amount_payload = self._with_tax_amount_details(
            without_details, tax_amount_anchor, self.changed_amount_details
        )
        self.changed_period_payload = self._with_tax_amount_details(
            without_details, tax_amount_anchor, self.changed_period_start_details
        )
        self.reordered_payload = self._with_tax_amount_details(
            without_details, tax_amount_anchor, self.reordered_details
        )

        base_detail_xml = self._tax_amount_detail_xml(self.base_details[0])
        reformatted_detail_xml = base_detail_xml.replace(
            b"                    <FrDt>2026-07-01</FrDt>\n",
            b"                    <FrDt>\n"
            b"                      2026-07-01\n"
            b"                    </FrDt>\n",
            1,
        )
        self.assertEqual(self.base_payload.count(base_detail_xml), 1)
        self.reformatted_payload = self.base_payload.replace(
            base_detail_xml,
            reformatted_detail_xml,
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_amount_statement = parse_bank_statement_payload(
            self.changed_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_period_statement = parse_bank_statement_payload(
            self.changed_period_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_tax_amount_details_are_repeatable_material_evidence(self) -> None:
        """Tax detail amount, period boundary, and source order must affect identity."""
        variants = (
            (self.base_statement, self.base_details),
            (self.changed_amount_statement, self.changed_amount_details),
            (self.changed_period_statement, self.changed_period_start_details),
            (self.reordered_statement, self.reordered_details),
        )
        hashes: list[str] = []
        for statement, details in variants:
            expected_hash = self._expected_hash(details)
            detail = statement.entries[0].entry_details[0]
            self.assertRegex(expected_hash, _HASH_PATTERN)
            self.assertEqual(
                getattr(detail, "detail_tax_evidence_hash", None),
                expected_hash,
            )
            TaxEvidenceRed._assert_entry_hash_binding(
                statement.entries[0], expected_hash
            )
            hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed in (
            self.changed_amount_statement,
            self.changed_period_statement,
            self.reordered_statement,
        ):
            base_detail = self.base_statement.entries[0].entry_details[0]
            changed_detail = changed.entries[0].entry_details[0]
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_amount,
                changed.entries[0].entry_amount,
            )
            self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
            self.assertNotEqual(
                base_detail.source_detail_hash,
                changed_detail.source_detail_hash,
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

    def test_tax_amount_detail_layout_is_not_semantic_identity(self) -> None:
        """Whitespace changes raw artifact provenance, not TaxRecordDetails3 semantics."""
        expected_hash = self._expected_hash(self.base_details)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "detail_tax_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_detail, "detail_tax_evidence_hash", None),
            expected_hash,
        )
        TaxEvidenceRed._assert_entry_hash_binding(
            self.base_statement.entries[0], expected_hash
        )
        TaxEvidenceRed._assert_entry_hash_binding(
            self.reformatted_statement.entries[0], expected_hash
        )
        self.assertEqual(
            base_detail.source_detail_hash,
            reformatted_detail.source_detail_hash,
        )
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_tax_amount_details_require_explicit_statement_correction(self) -> None:
        """Changed TaxRecordDetails3 cannot silently replace accepted source evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, payload in (
            ("amount", self.changed_amount_payload),
            ("period-start", self.changed_period_payload),
            ("order", self.reordered_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(
                    AccountingValidationError, _CORRECTION_ERROR
                ):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_tax_amount_details_without_recomputing_amount_truth(
        self,
    ) -> None:
        """Buyer read exposes source tax breakdown while accounting amount stays distinct."""
        expected_hash = self._expected_hash(self.base_details)
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
        self.assertEqual(
            records[0].get("details"),
            [
                {
                    "period_year": "2026",
                    "period_type": "MM07",
                    "period_from_date": "2026-07-01",
                    "period_to_date": "2026-07-31",
                    "amount": "1200.00",
                    "currency_code": "KRW",
                },
                {
                    "period_year": "2026",
                    "period_type": "MM08",
                    "period_from_date": "2026-08-01",
                    "period_to_date": "2026-08-31",
                    "amount": "1300.00",
                    "currency_code": "KRW",
                },
            ],
        )
        self.assertEqual(records[0].get("total_amount"), "2500.00")
        self.assertEqual(records[0].get("total_amount_currency_code"), "KRW")
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _expected_hash(self, details: tuple[TaxAmountDetail, ...]) -> str:
        """Digest TaxData1 with repeatable TaxRecordDetails3 in source order."""
        records = self.tax["records"]
        if not isinstance(records, tuple) or len(records) != 1:
            raise AssertionError("test tax records must contain one immutable record")
        record = records[0]
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
                        "details": [
                            {
                                "period_year": item[0],
                                "period_type": item[1],
                                "period_from_date": item[2],
                                "period_to_date": item[3],
                                "amount": item[4],
                                "currency_code": item[5],
                            }
                            for item in details
                        ],
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _tax_amount_detail_xml(detail: TaxAmountDetail) -> bytes:
        """Return one schema-ordered TaxRecordDetails3 fragment."""
        return (
            "                  <Dtls>\n"
            "                    <Prd>\n"
            f"                      <Yr>{detail[0]}</Yr>\n"
            f"                      <Tp>{detail[1]}</Tp>\n"
            "                      <FrToDt>\n"
            f"                        <FrDt>{detail[2]}</FrDt>\n"
            f"                        <ToDt>{detail[3]}</ToDt>\n"
            "                      </FrToDt>\n"
            "                    </Prd>\n"
            f"                    <Amt Ccy=\"{detail[5]}\">{detail[4]}</Amt>\n"
            "                  </Dtls>\n"
        ).encode("utf-8")

    @classmethod
    def _with_tax_amount_details(
        cls,
        payload: bytes,
        anchor: bytes,
        details: tuple[TaxAmountDetail, ...],
    ) -> bytes:
        """Insert repeatable TaxRecordDetails3 after TaxAmount3 totals."""
        rendered = b"".join(cls._tax_amount_detail_xml(detail) for detail in details)
        replacement = anchor.replace(
            b"                </TaxAmt>\n",
            rendered + b"                </TaxAmt>\n",
            1,
        )
        return payload.replace(anchor, replacement, 1)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with stable statement identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-tax-record-details-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
