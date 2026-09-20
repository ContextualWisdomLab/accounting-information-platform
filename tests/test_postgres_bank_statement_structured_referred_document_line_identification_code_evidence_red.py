"""REDs for coded referred-document line-identification type evidence preservation."""

from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
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


class BankStatementStructuredLineIdentificationCodeEvidenceRedTests(unittest.TestCase):
    """Retain LineDtls/Id/Tp/CdOrPrtry/Cd with the exact source line identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        line_details_contract.BankStatementStructuredLineDetailsEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two-line statements differing only in the second line type code."""
        self.line_contract = (
            line_details_contract.BankStatementStructuredLineDetailsEvidenceRedTests(
                "test_line_projection_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.line_contract.setUp()
        self.addCleanup(self.line_contract.doCleanups)

        self.base_line_type_code = "SKNB"
        self.changed_line_type_code = "PRNB"
        self.base_payload = self.line_contract.base_payload
        self.changed_payload = self._replace_second_line_type_code(
            self.base_payload,
            self.base_line_type_code,
            self.changed_line_type_code,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

    def test_line_identification_code_is_material_to_each_canonical_evidence_hash(self) -> None:
        """Bind a coded line-type-only source change to every canonical evidence hash."""
        self.assertEqual(
            self._replace_second_line_type_code(
                self.changed_payload,
                self.changed_line_type_code,
                self.base_line_type_code,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(self.base_line_type_code)
        changed_projection = self._structured_projection(self.changed_line_type_code)

        self.assertNotEqual(base_projection, changed_projection)
        self._assert_non_structured_normalization_unchanged()
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

    def test_changed_line_identification_code_fails_closed_for_same_statement_identity(self) -> None:
        """A line-type-code-only change fails closed without correction authority."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(self.base_payload, "line-type-code-base"),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "line-type-code-changed",
                ),
                posting.DATABASE_URL,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

    def test_buyer_read_keeps_line_identification_code_bound_to_exact_line(self) -> None:
        """Buyer reads retain the changed coded type on the exact second source line."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-type-code-lookup",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]

        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self._structured_projection(self.changed_line_type_code),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove the source delta cannot hide collateral normalized-field changes."""
        statement_fields = (
            "message_definition_identifier",
            "statement_identity_reference",
            "electronic_sequence_number",
            "legal_sequence_number",
            "period_start_at",
            "period_end_at",
            "opening_balance_hash",
            "closing_balance_hash",
            "account_currency_code",
            "account_identifier_hash",
        )
        self.assertEqual(
            tuple(getattr(self.base_statement, field) for field in statement_fields),
            tuple(getattr(self.changed_statement, field) for field in statement_fields),
        )
        self.assertEqual(len(self.base_statement.entries), len(self.changed_statement.entries))

        entry_fields = (
            "source_entry_identity",
            "entry_sequence_number",
            "source_locator_path",
            "booking_occurred_at",
            "value_occurred_at",
            "entry_amount",
            "entry_currency_code",
            "credit_debit_code",
            "reversal_indicator",
            "bank_transaction_domain_code",
            "bank_transaction_family_code",
            "bank_transaction_subfamily_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "mandate_reference",
            "cheque_reference",
            "remittance_evidence_text",
            "counterparty_evidence_hash",
        )
        detail_fields = (
            "detail_sequence_number",
            "source_locator_path",
            "detail_amount",
            "detail_currency_code",
            "credit_debit_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "remittance_evidence_text",
        )
        for base_entry, changed_entry in zip(
            self.base_statement.entries,
            self.changed_statement.entries,
            strict=True,
        ):
            self.assertEqual(
                tuple(getattr(base_entry, field) for field in entry_fields),
                tuple(getattr(changed_entry, field) for field in entry_fields),
            )
            self.assertEqual(len(base_entry.entry_details), len(changed_entry.entry_details))
            for base_detail, changed_detail in zip(
                base_entry.entry_details,
                changed_entry.entry_details,
                strict=True,
            ):
                self.assertEqual(
                    tuple(getattr(base_detail, field) for field in detail_fields),
                    tuple(getattr(changed_detail, field) for field in detail_fields),
                )

    def _structured_projection(self, second_line_type_code: str) -> list[dict[str, object]]:
        """Return the ordered line projection with only the second coded type changed."""
        projection = deepcopy(
            self.line_contract._structured_projection(
                self.line_contract.second_line_number,
                self.line_contract.second_line_description,
            )
        )
        line_details = projection[0]["line_details"]
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("canonical line projection must contain exactly two lines")
        first_line = line_details[0]
        second_line = line_details[1]
        if not isinstance(first_line, dict) or not isinstance(second_line, dict):
            raise AssertionError("canonical line projection entries must be mappings")
        if first_line.get("line_type_code") != self.base_line_type_code:
            raise AssertionError("first source line must retain canonical SKNB type")
        if second_line.get("line_type_code") != self.base_line_type_code:
            raise AssertionError("parent second source line must begin with canonical SKNB type")
        second_line["line_type_code"] = second_line_type_code
        return projection

    def _replace_second_line_type_code(
        self,
        payload: bytes,
        old_type_code: str,
        new_type_code: str,
    ) -> bytes:
        """Replace only Id/Tp/CdOrPrtry/Cd inside the exact second source line."""
        text = payload.decode("utf-8")
        line_marker = f"<Nb>{self.line_contract.second_line_number}</Nb>"
        if text.count(line_marker) != 1:
            raise AssertionError("second line number marker must occur exactly once")
        marker_index = text.index(line_marker)
        line_start = text.rfind("<LineDtls>", 0, marker_index)
        line_end_start = text.find("</LineDtls>", marker_index)
        if line_start < 0 or line_end_start < 0:
            raise AssertionError("second line boundaries must be present")
        line_end = line_end_start + len("</LineDtls>")
        line_segment = text[line_start:line_end]

        old_markup = (
            "<CdOrPrtry>\n"
            f"                          <Cd>{old_type_code}</Cd>\n"
            "                        </CdOrPrtry>"
        )
        new_markup = (
            "<CdOrPrtry>\n"
            f"                          <Cd>{new_type_code}</Cd>\n"
            "                        </CdOrPrtry>"
        )
        if line_segment.count(old_markup) != 1:
            raise AssertionError("second line coded type marker must occur exactly once")
        if old_markup != new_markup and line_segment.count(new_markup) != 0:
            raise AssertionError("replacement coded type must not pre-exist in second line")

        changed_line = line_segment.replace(old_markup, new_markup, 1)
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
