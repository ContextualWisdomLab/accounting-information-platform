"""REDs for referred-document type-code evidence preservation."""

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
    test_postgres_bank_statement_structured_referred_document_line_related_date_evidence_red
    as related_date_contract,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredReferredDocumentTypeCodeEvidenceRedTests(unittest.TestCase):
    """Retain RfrdDocInf/Tp/CdOrPrtry/Cd as reconciliation source evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        related_date_contract.BankStatementStructuredLineRelatedDateEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements differing only in the referred-document type code."""
        self.related_date_contract = (
            related_date_contract.BankStatementStructuredLineRelatedDateEvidenceRedTests(
                "test_line_related_date_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.related_date_contract.setUp()
        self.addCleanup(self.related_date_contract.doCleanups)
        self.line_contract = self.related_date_contract.line_contract

        self.base_document_type_code = "CINV"
        self.changed_document_type_code = "CREN"
        self.document_number = self.line_contract.document_number
        self.base_payload = self.related_date_contract.base_payload
        self.changed_payload = self._replace_document_type_code(
            self.base_payload,
            self.base_document_type_code,
            self.changed_document_type_code,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

    def test_document_type_code_is_material_to_each_canonical_evidence_hash(self) -> None:
        """Bind a document-type-code-only change to every canonical evidence hash."""
        self.assertEqual(
            self._replace_document_type_code(
                self.changed_payload,
                self.changed_document_type_code,
                self.base_document_type_code,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(self.base_document_type_code)
        changed_projection = self._structured_projection(self.changed_document_type_code)

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

    def test_changed_document_type_code_fails_closed_for_same_statement_identity(self) -> None:
        """A document-type-only change fails closed without correction authority."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(self.base_payload, "document-type-code-base"),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "document-type-code-changed",
                ),
                posting.DATABASE_URL,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

    def test_buyer_read_keeps_document_type_code_bound_to_exact_document(self) -> None:
        """Buyer reads retain the changed type on the exact referred document."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "document-type-code-lookup",
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
            self._structured_projection(self.changed_document_type_code),
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

    def _structured_projection(self, document_type_code: str) -> list[dict[str, object]]:
        """Return the ordered projection with only the document type code changed."""
        projection = deepcopy(
            self.related_date_contract._structured_projection(
                self.related_date_contract.base_related_date
            )
        )
        if len(projection) != 1 or not isinstance(projection[0], dict):
            raise AssertionError("canonical projection must contain one referred document")
        referred_document = projection[0]
        if referred_document.get("document_type_code") != self.base_document_type_code:
            raise AssertionError("parent referred document must begin with CINV type")
        if referred_document.get("document_number") != self.document_number:
            raise AssertionError("referred-document number must remain stable")
        referred_document["document_type_code"] = document_type_code
        return projection

    def _replace_document_type_code(
        self,
        payload: bytes,
        old_type_code: str,
        new_type_code: str,
    ) -> bytes:
        """Replace only RfrdDocInf/Tp/CdOrPrtry/Cd for the exact source document."""
        text = payload.decode("utf-8")
        document_marker = f"<Nb>{self.document_number}</Nb>"
        if text.count(document_marker) != 1:
            raise AssertionError("referred-document number marker must occur exactly once")
        marker_index = text.index(document_marker)
        document_start = text.rfind("<RfrdDocInf>", 0, marker_index)
        document_end_start = text.find("</RfrdDocInf>", marker_index)
        if document_start < 0 or document_end_start < 0:
            raise AssertionError("referred-document boundaries must be present")
        document_end = document_end_start + len("</RfrdDocInf>")
        document_segment = text[document_start:document_end]

        old_markup = (
            "<CdOrPrtry>\n"
            f"                      <Cd>{old_type_code}</Cd>\n"
            "                    </CdOrPrtry>"
        )
        new_markup = (
            "<CdOrPrtry>\n"
            f"                      <Cd>{new_type_code}</Cd>\n"
            "                    </CdOrPrtry>"
        )
        if document_segment.count(old_markup) != 1:
            raise AssertionError("document coded type marker must occur exactly once")
        if old_markup != new_markup and document_segment.count(new_markup) != 0:
            raise AssertionError("replacement document type must not pre-exist")

        changed_document = document_segment.replace(old_markup, new_markup, 1)
        return (text[:document_start] + changed_document + text[document_end:]).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
