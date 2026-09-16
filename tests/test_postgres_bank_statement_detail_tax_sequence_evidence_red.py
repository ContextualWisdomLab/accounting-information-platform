"""PostgreSQL REDs for camt.053 TaxData1 sequence-number evidence."""

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


class BankStatementDetailTaxSequenceEvidenceRedTests(unittest.TestCase):
    """Keep TaxData1 sequence number in bank evidence without making it journal truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build sequence-number variants while holding every monetary fact fixed."""
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
        without_sequence = TaxEvidenceRed._with_tax(fixture, marker, self.tax)
        sequence_anchor = (
            b"              <Dt>2026-08-24</Dt>\n"
            b"              <Rcrd>"
        )
        self.assertEqual(without_sequence.count(sequence_anchor), 1)
        self.base_payload = without_sequence.replace(
            sequence_anchor,
            b"              <Dt>2026-08-24</Dt>\n"
            b"              <SeqNb>1</SeqNb>\n"
            b"              <Rcrd>",
            1,
        )
        self.changed_payload = without_sequence.replace(
            sequence_anchor,
            b"              <Dt>2026-08-24</Dt>\n"
            b"              <SeqNb>2</SeqNb>\n"
            b"              <Rcrd>",
            1,
        )
        self.assertNotEqual(self.base_payload, self.changed_payload)

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_tax_sequence_number_is_independently_material_evidence(self) -> None:
        """Changing only SeqNb must change purpose, detail, entry, and statement identity."""
        base_hash = self._expected_hash("1")
        changed_hash = self._expected_hash("2")
        base_detail = self.base_statement.entries[0].entry_details[0]
        changed_detail = self.changed_statement.entries[0].entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertRegex(changed_hash, _HASH_PATTERN)
        self.assertNotEqual(base_hash, changed_hash)
        self.assertEqual(getattr(base_detail, "detail_tax_evidence_hash", None), base_hash)
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

    def test_changed_tax_sequence_requires_explicit_statement_correction(self) -> None:
        """A changed bank-reported SeqNb cannot silently replace accepted evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "sequence"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_tax_sequence_without_recomputing_amount_truth(self) -> None:
        """Buyer read exposes SeqNb while entry/detail accounting amount remains independent."""
        expected_hash = self._expected_hash("1")
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
        self.assertEqual(tax_evidence.get("sequence_number"), "1")
        self.assertEqual(tax_evidence.get("total_tax_amount"), "2500.00")
        self.assertEqual(tax_evidence.get("total_tax_currency_code"), "KRW")
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _expected_hash(self, sequence_number: str) -> str:
        """Digest TaxData1 semantics with SeqNb as its own source evidence field."""
        records = self.tax["records"]
        if not isinstance(records, tuple):
            raise AssertionError("test tax records must be an immutable tuple")
        preimage = json.dumps(
            {
                "evidence_type": _DETAIL_TAX_PURPOSE,
                "administration_zone": self.tax["administration_zone"],
                "reference_number": self.tax["reference_number"],
                "method": self.tax["method"],
                "total_taxable_base_amount": self.tax["total_taxable_base_amount"],
                "total_taxable_base_currency_code": self.tax[
                    "total_taxable_base_currency_code"
                ],
                "total_tax_amount": self.tax["total_tax_amount"],
                "total_tax_currency_code": self.tax["total_tax_currency_code"],
                "tax_date": self.tax["tax_date"],
                "sequence_number": sequence_number,
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

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build the supported ingest command with a stable statement identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "bank_statement_reference": "urn:cwl:bank_statement:detail-tax-sequence-red",
            "statement_payload": payload,
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "idempotency_key": f"detail-tax-sequence-red-{suffix}-{uuid.uuid4().hex}",
        }


if __name__ == "__main__":
    unittest.main()
