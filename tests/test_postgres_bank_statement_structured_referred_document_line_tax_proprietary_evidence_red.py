"""REDs for proprietary line-level tax-type evidence preservation."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal

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
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_details_evidence_red
    as line_details_contract,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_tax_evidence_red
    as line_tax_contract,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineTaxProprietaryEvidenceRedTests(unittest.TestCase):
    """Retain LineDtls tax proprietary types with their exact source line and amount."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare line-tax statements differing only in one proprietary type value."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.document_number = "INV-2026-1001"
        self.line_related_date = "2026-09-01"
        self.first_line_number = "Stockitem1"
        self.second_line_number = "Stockitem2"
        self.first_line_description = "Annual support service"
        self.second_line_description = "Quarterly data service"
        self.base_tax_type_proprietary = "MUNICIPAL_SURCHARGE"
        self.changed_tax_type_proprietary = "REGIONAL_SURCHARGE"
        self.second_local_tax_amount = Decimal("50.00")

        self.line_contract = (
            line_details_contract.BankStatementStructuredLineDetailsEvidenceRedTests(
                "test_line_projection_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.line_contract.document_number = self.document_number
        self.line_contract.first_line_number = self.first_line_number
        self.line_contract.first_line_description = self.first_line_description
        self.line_contract.line_related_date = self.line_related_date

        line_payload = self.line_contract._with_line_details(
            fixture,
            marker,
            self.second_line_number,
            self.second_line_description,
        )
        tax_contract = line_tax_contract.BankStatementStructuredLineTaxEvidenceRedTests(
            "test_later_line_tax_is_material_to_each_canonical_evidence_hash"
        )
        coded_payload = tax_contract._with_line_taxes(
            line_payload,
            self.line_contract._decimal_text(self.second_local_tax_amount),
        )
        self.base_payload = self._replace_second_line_tax_type(
            coded_payload,
            "<Tp><Cd>LOCL</Cd></Tp>",
            f"<Tp><Prtry>{self.base_tax_type_proprietary}</Prtry></Tp>",
        )
        self.changed_payload = self._replace_second_line_tax_type(
            self.base_payload,
            f"<Tp><Prtry>{self.base_tax_type_proprietary}</Prtry></Tp>",
            f"<Tp><Prtry>{self.changed_tax_type_proprietary}</Prtry></Tp>",
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = (
            f"urn:cwl:bank_account:structured-line-tax-proprietary:{uuid.uuid4().hex}"
        )
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

    def test_line_tax_proprietary_type_is_material_to_each_canonical_evidence_hash(
        self,
    ) -> None:
        """Bind a proprietary-type-only source change to the complete evidence chain."""
        self.assertEqual(
            self._replace_second_line_tax_type(
                self.changed_payload,
                f"<Tp><Prtry>{self.changed_tax_type_proprietary}</Prtry></Tp>",
                f"<Tp><Prtry>{self.base_tax_type_proprietary}</Prtry></Tp>",
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(self.base_tax_type_proprietary)
        changed_projection = self._structured_projection(self.changed_tax_type_proprietary)

        self.assertNotEqual(base_projection, changed_projection)
        for value in (
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        ):
            self.line_contract._assert_sha256(value)

        self.assertEqual(
            base_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(base_detail, base_projection),
        )
        self.assertEqual(
            changed_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(changed_detail, changed_projection),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(base_entry, {1: base_projection}),
        )
        self.assertEqual(
            changed_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(changed_entry, {1: changed_projection}),
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.base_statement,
                {(1, 1): base_projection},
            ),
        )
        self.assertEqual(
            self.changed_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.changed_statement,
                {(1, 1): changed_projection},
            ),
        )

        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.line_contract._expected_entry_hash(self.base_statement.entries[1], {}),
        )
        self.line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        self.line_contract._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_changed_line_tax_proprietary_type_requires_explicit_statement_correction(
        self,
    ) -> None:
        """Proprietary tax-type changes cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "line-tax-proprietary-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(
                    self.changed_payload,
                    "line-tax-proprietary-changed",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_proprietary_tax_type_bound_to_the_exact_line(self) -> None:
        """Buyer reads retain the proprietary tax type with its line, amount, and currency."""
        accepted = accept_bank_statement_evidence(
            self._command(self.changed_payload, "line-tax-proprietary-lookup"),
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

        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self._structured_projection(self.changed_tax_type_proprietary),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _structured_projection(self, second_tax_type_proprietary: str) -> list[dict[str, object]]:
        """Return the exact mixed coded/proprietary line-tax projection."""
        projection = self.line_contract._structured_projection(
            self.second_line_number,
            self.second_line_description,
        )
        line_details = projection[0]["line_details"]
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("canonical line projection must contain exactly two lines")

        line_details[0]["tax_amounts"] = [
            {
                "type_code": "STAT",
                "amount": "1900",
                "currency_code": "KRW",
            }
        ]
        line_details[1]["tax_amounts"] = [
            {
                "type_code": "STAT",
                "amount": "950",
                "currency_code": "KRW",
            },
            {
                "type_proprietary": second_tax_type_proprietary,
                "amount": self.line_contract._decimal_text(self.second_local_tax_amount),
                "currency_code": "KRW",
            },
        ]
        return projection

    def _replace_second_line_tax_type(
        self,
        payload: bytes,
        old_type_xml: str,
        new_type_xml: str,
    ) -> bytes:
        """Replace one tax type only inside the uniquely identified second source line."""
        text = payload.decode("utf-8")
        line_number_marker = f"<Nb>{self.second_line_number}</Nb>"
        if text.count(line_number_marker) != 1:
            raise AssertionError("second line number must occur exactly once")

        marker_index = text.index(line_number_marker)
        line_start = text.rfind("<LineDtls>", 0, marker_index)
        line_end_start = text.find("</LineDtls>", marker_index)
        if line_start < 0 or line_end_start < 0:
            raise AssertionError("second LineDtls boundaries must be present")
        line_end = line_end_start + len("</LineDtls>")
        segment = text[line_start:line_end]
        if segment.count(old_type_xml) != 1:
            raise AssertionError("target second-line tax type must occur exactly once")
        if old_type_xml != new_type_xml and segment.count(new_type_xml) != 0:
            raise AssertionError("replacement second-line tax type must not pre-exist")

        replaced_segment = segment.replace(old_type_xml, new_type_xml, 1)
        return (text[:line_start] + replaced_segment + text[line_end:]).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"structured-line-tax-proprietary-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
