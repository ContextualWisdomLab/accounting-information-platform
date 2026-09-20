"""REDs for proprietary referred-document line-identification type evidence."""

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

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineTypeProprietaryEvidenceRedTests(unittest.TestCase):
    """Retain LineDtls/Id/Tp/CdOrPrtry/Prtry as source reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two-line statements differing only in one proprietary line type."""
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
        self.base_second_line_type_proprietary = "SUPPLIER_LINE"
        self.changed_second_line_type_proprietary = "WAREHOUSE_LINE"

        self.line_contract = (
            line_details_contract.BankStatementStructuredLineDetailsEvidenceRedTests(
                "test_line_projection_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.line_contract.document_number = self.document_number
        self.line_contract.first_line_number = self.first_line_number
        self.line_contract.first_line_description = self.first_line_description
        self.line_contract.line_related_date = self.line_related_date

        coded_payload = self.line_contract._with_line_details(
            fixture,
            marker,
            self.second_line_number,
            self.second_line_description,
        )
        coded_markup = (
            "<CdOrPrtry>\n"
            "                          <Cd>SKNB</Cd>\n"
            "                        </CdOrPrtry>"
        )
        base_proprietary_markup = (
            "<CdOrPrtry>\n"
            f"                          <Prtry>{self.base_second_line_type_proprietary}</Prtry>\n"
            "                        </CdOrPrtry>"
        )
        changed_proprietary_markup = (
            "<CdOrPrtry>\n"
            f"                          <Prtry>{self.changed_second_line_type_proprietary}</Prtry>\n"
            "                        </CdOrPrtry>"
        )
        self.base_payload = self._replace_second_line_type_choice(
            coded_payload,
            coded_markup,
            base_proprietary_markup,
        )
        self.changed_payload = self._replace_second_line_type_choice(
            self.base_payload,
            base_proprietary_markup,
            changed_proprietary_markup,
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
            "urn:cwl:bank_account:structured-line-type-proprietary:"
            f"{uuid.uuid4().hex}"
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

    def test_proprietary_line_type_is_material_to_each_canonical_evidence_hash(self) -> None:
        """Bind a proprietary line-type-only source change to every evidence hash."""
        changed_markup = (
            "<CdOrPrtry>\n"
            f"                          <Prtry>{self.changed_second_line_type_proprietary}</Prtry>\n"
            "                        </CdOrPrtry>"
        )
        base_markup = (
            "<CdOrPrtry>\n"
            f"                          <Prtry>{self.base_second_line_type_proprietary}</Prtry>\n"
            "                        </CdOrPrtry>"
        )
        self.assertEqual(
            self._replace_second_line_type_choice(
                self.changed_payload,
                changed_markup,
                base_markup,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(
            self.base_second_line_type_proprietary
        )
        changed_projection = self._structured_projection(
            self.changed_second_line_type_proprietary
        )

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
            self.line_contract._expected_entry_hash(
                changed_entry,
                {1: changed_projection},
            ),
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

    def test_changed_proprietary_line_type_requires_explicit_statement_correction(self) -> None:
        """A proprietary line-type-only change cannot silently replay a statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "line-type-proprietary-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(
                    self.changed_payload,
                    "line-type-proprietary-changed",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_proprietary_line_type_bound_to_exact_line(self) -> None:
        """Buyer reads retain the type choice with the line identity it qualifies."""
        accepted = accept_bank_statement_evidence(
            self._command(
                self.changed_payload,
                "line-type-proprietary-lookup",
            ),
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
            self._structured_projection(self.changed_second_line_type_proprietary),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _structured_projection(
        self,
        second_line_type_proprietary: str,
    ) -> list[dict[str, object]]:
        """Return one ordered mixed code/proprietary line-identification projection."""
        projection = self.line_contract._structured_projection(
            self.second_line_number,
            self.second_line_description,
        )
        line_details = projection[0]["line_details"]
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("canonical line projection must contain exactly two lines")
        second_line = line_details[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second line projection must be a mapping")
        if second_line.pop("line_type_code", None) != "SKNB":
            raise AssertionError("second line must begin with the canonical SKNB code")
        second_line["line_type_proprietary"] = second_line_type_proprietary
        return projection

    def _replace_second_line_type_choice(
        self,
        payload: bytes,
        current_type_markup: str,
        replacement_type_markup: str,
    ) -> bytes:
        """Replace Id/Tp/CdOrPrtry only inside the unique second source line."""
        text = payload.decode("utf-8")
        line_marker = f"<Nb>{self.second_line_number}</Nb>"
        if text.count(line_marker) != 1:
            raise AssertionError("second line number marker must occur exactly once")
        marker_position = text.index(line_marker)
        line_start = text.rindex("<LineDtls>", 0, marker_position)
        line_end = text.index("</LineDtls>", marker_position)
        line_segment = text[line_start:line_end]
        if line_segment.count(current_type_markup) != 1:
            raise AssertionError(
                "second line identification type choice marker must occur exactly once"
            )
        changed_segment = line_segment.replace(
            current_type_markup,
            replacement_type_markup,
            1,
        )
        return (text[:line_start] + changed_segment + text[line_end:]).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"structured-line-type-proprietary-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
