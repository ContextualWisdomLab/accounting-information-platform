"""REDs for referred-document type-issuer evidence preservation."""

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
    test_postgres_bank_statement_structured_referred_document_type_proprietary_evidence_red
    as proprietary_type_contract,
)

_PARENT_TEST = (
    proprietary_type_contract.
    BankStatementStructuredReferredDocumentTypeProprietaryEvidenceRedTests
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredReferredDocumentTypeIssuerEvidenceRedTests(
    unittest.TestCase
):
    """Retain a non-first RfrdDocInf/Tp/Issr beside competing issuer scopes."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare two-document statements differing only in the second type issuer."""
        self.proprietary_contract = (
            _PARENT_TEST(
                "test_document_type_proprietary_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.proprietary_contract.setUp()
        self.addCleanup(self.proprietary_contract.doCleanups)
        self.line_contract = self.proprietary_contract.line_contract

        self.first_document_number = self.proprietary_contract.first_document_number
        self.second_document_number = self.proprietary_contract.second_document_number
        self.document_type_code = (
            self.proprietary_contract.type_code_contract.base_document_type_code
        )
        self.base_document_type_issuer = "ISO"
        self.changed_document_type_issuer = "LOCAL-SCHEME"
        self.line_type_issuer_decoy = "LINE-SCHEME"

        self.decoy_only_payload = self._with_second_document_line_issuer(
            self.proprietary_contract.coded_payload,
            self.line_type_issuer_decoy,
        )
        self.base_payload = self._with_document_type_issuer(
            self.decoy_only_payload,
            self.base_document_type_issuer,
        )
        self.changed_payload = self._replace_document_type_issuer(
            self.base_payload,
            self.base_document_type_issuer,
            self.changed_document_type_issuer,
        )
        self.decoy_only_statement = parse_bank_statement_payload(
            self.decoy_only_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

    def test_document_type_issuer_is_material_to_each_canonical_evidence_hash(
        self,
    ) -> None:
        """Bind a referred-document type-issuer-only change to every evidence hash."""
        self.assertEqual(
            self._replace_document_type_issuer(
                self.changed_payload,
                self.changed_document_type_issuer,
                self.base_document_type_issuer,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(
            self.base_document_type_issuer
        )
        changed_projection = self._structured_projection(
            self.changed_document_type_issuer
        )

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
            self.line_contract._expected_detail_hash(
                changed_detail,
                changed_projection,
            ),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(
                base_entry,
                {1: base_projection},
            ),
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

        self.assertNotEqual(
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
        )
        self.assertNotEqual(
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
        )
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
            self.line_contract._expected_entry_hash(
                self.base_statement.entries[1],
                {},
            ),
        )
        self.line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        self.line_contract._assert_exact_transaction_amount(
            changed_entry,
            changed_detail,
        )

    def test_line_issuer_decoy_does_not_populate_document_type_issuer(self) -> None:
        """A line Id/Issr remains line evidence when outer Tp/Issr is absent."""
        entry = self.decoy_only_statement.entries[0]
        detail = entry.entry_details[0]
        projection = self._structured_projection(None)

        self.assertEqual(
            detail.source_detail_hash,
            self.line_contract._expected_detail_hash(detail, projection),
        )
        self.assertEqual(
            entry.source_entry_hash,
            self.line_contract._expected_entry_hash(entry, {1: projection}),
        )
        self.assertEqual(
            self.decoy_only_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.decoy_only_statement,
                {(1, 1): projection},
            ),
        )
        self.line_contract._assert_exact_transaction_amount(entry, detail)

        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.decoy_only_payload,
                "document-type-issuer-decoy-only",
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
        buyer_entry = document["bank_statement_entries"][0]
        buyer_detail = buyer_entry["entry_details"][0]
        self.assertEqual(
            buyer_detail.get(_STRUCTURED_EVIDENCE_KEY),
            projection,
        )
        self.assertEqual(
            Decimal(str(buyer_entry["entry_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(buyer_entry["entry_currency_code"], "KRW")
        self.assertEqual(
            Decimal(str(buyer_detail["detail_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(buyer_detail["detail_currency_code"], "KRW")

    def test_changed_document_type_issuer_fails_closed_for_same_statement_identity(
        self,
    ) -> None:
        """A type-issuer-only change fails closed without correction authority."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "document-type-issuer-base",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "document-type-issuer-changed",
                ),
                posting.DATABASE_URL,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

    def test_buyer_read_keeps_document_type_issuer_bound_to_exact_document(
        self,
    ) -> None:
        """Buyer reads retain each issuer at its exact source scope and document."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "document-type-issuer-lookup",
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
            self._structured_projection(self.changed_document_type_issuer),
        )
        self.assertEqual(
            Decimal(str(entry["entry_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(
            Decimal(str(detail["detail_amount"])),
            Decimal("25000.00"),
        )
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
            tuple(
                getattr(self.base_statement, field)
                for field in statement_fields
            ),
            tuple(
                getattr(self.changed_statement, field)
                for field in statement_fields
            ),
        )
        self.assertEqual(
            len(self.base_statement.entries),
            len(self.changed_statement.entries),
        )

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
                tuple(
                    getattr(base_entry, field)
                    for field in entry_fields
                ),
                tuple(
                    getattr(changed_entry, field)
                    for field in entry_fields
                ),
            )
            self.assertEqual(
                len(base_entry.entry_details),
                len(changed_entry.entry_details),
            )
            for base_detail, changed_detail in zip(
                base_entry.entry_details,
                changed_entry.entry_details,
                strict=True,
            ):
                self.assertEqual(
                    tuple(
                        getattr(base_detail, field)
                        for field in detail_fields
                    ),
                    tuple(
                        getattr(changed_detail, field)
                        for field in detail_fields
                    ),
                )

    def _structured_projection(
        self,
        document_type_issuer: str | None,
    ) -> list[dict[str, object]]:
        """Return two documents with competing issuer scopes on the second."""
        parent_projection = deepcopy(
            self.proprietary_contract.type_code_contract._structured_projection(
                self.document_type_code
            )
        )
        if len(parent_projection) != 1 or not isinstance(
            parent_projection[0],
            dict,
        ):
            raise AssertionError(
                "parent projection must contain one referred document"
            )
        first_document = parent_projection[0]
        if first_document.get("document_type_code") != self.document_type_code:
            raise AssertionError(
                "first referred document must retain the coded type"
            )
        if first_document.get("document_number") != self.first_document_number:
            raise AssertionError(
                "first referred-document number must remain stable"
            )

        second_document = deepcopy(first_document)
        second_document["document_number"] = self.second_document_number
        line_details = second_document.get("line_details")
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError(
                "second referred document must retain exactly two source lines"
            )
        first_line = line_details[0]
        if not isinstance(first_line, dict):
            raise AssertionError("first second-document line must be a mapping")
        first_line["line_type_issuer"] = self.line_type_issuer_decoy
        if document_type_issuer is not None:
            second_document["document_type_issuer"] = document_type_issuer
        return [first_document, second_document]

    def _with_second_document_line_issuer(
        self,
        payload: bytes,
        issuer: str,
    ) -> bytes:
        """Insert a decoy LineDtls/Id/Issr only inside the second document."""
        text, document_start, document_end, document_segment = (
            self._document_segment(payload)
        )
        line_number = self.line_contract.first_line_number
        line_marker = f"<Nb>{line_number}</Nb>"
        if document_segment.count(line_marker) != 1:
            raise AssertionError(
                "second-document first-line marker must occur exactly once"
            )
        marker_index = document_segment.index(line_marker)
        line_start = document_segment.rfind("<LineDtls>", 0, marker_index)
        line_end_start = document_segment.find("</LineDtls>", marker_index)
        if line_start < 0 or line_end_start < 0:
            raise AssertionError("second-document first-line boundaries must exist")
        line_end = line_end_start + len("</LineDtls>")
        line_segment = document_segment[line_start:line_end]
        if "<Issr>" in line_segment:
            raise AssertionError("line issuer decoy must not pre-exist")
        marker = (
            "                      </Tp>\n"
            f"                      <Nb>{line_number}</Nb>"
        )
        if line_segment.count(marker) != 1:
            raise AssertionError(
                "line identification type/number marker must occur exactly once"
            )
        replacement = (
            "                      </Tp>\n"
            f"                      <Issr>{issuer}</Issr>\n"
            f"                      <Nb>{line_number}</Nb>"
        )
        changed_line = line_segment.replace(marker, replacement, 1)
        changed_document = (
            document_segment[:line_start]
            + changed_line
            + document_segment[line_end:]
        )
        return (
            text[:document_start] + changed_document + text[document_end:]
        ).encode("utf-8")

    def _with_document_type_issuer(
        self,
        payload: bytes,
        issuer: str,
    ) -> bytes:
        """Insert one Issr under only the second document's outer Tp element."""
        text, document_start, document_end, document_segment = (
            self._document_segment(payload)
        )
        type_start, type_end, type_segment = self._outer_type_segment(
            document_segment
        )
        if "<Issr>" in type_segment:
            raise AssertionError(
                "outer referred-document type issuer must not pre-exist"
            )
        type_close = type_segment.rfind("</Tp>")
        if type_close < 0:
            raise AssertionError(
                "outer referred-document type closing tag must be present"
            )
        issuer_markup = f"<Issr>{issuer}</Issr>\n                    "
        changed_type = (
            type_segment[:type_close]
            + issuer_markup
            + type_segment[type_close:]
        )
        changed_document = (
            document_segment[:type_start]
            + changed_type
            + document_segment[type_end:]
        )
        return (
            text[:document_start] + changed_document + text[document_end:]
        ).encode("utf-8")

    def _replace_document_type_issuer(
        self,
        payload: bytes,
        old_issuer: str,
        new_issuer: str,
    ) -> bytes:
        """Replace only the second document's outer Tp/Issr value."""
        text, document_start, document_end, document_segment = (
            self._document_segment(payload)
        )
        type_start, type_end, type_segment = self._outer_type_segment(
            document_segment
        )
        old_markup = f"<Issr>{old_issuer}</Issr>"
        new_markup = f"<Issr>{new_issuer}</Issr>"
        if type_segment.count(old_markup) != 1:
            raise AssertionError(
                "outer document type issuer marker must occur exactly once"
            )
        if old_markup != new_markup and type_segment.count(new_markup) != 0:
            raise AssertionError(
                "replacement outer document type issuer must not pre-exist"
            )
        changed_type = type_segment.replace(old_markup, new_markup, 1)
        changed_document = (
            document_segment[:type_start]
            + changed_type
            + document_segment[type_end:]
        )
        return (
            text[:document_start] + changed_document + text[document_end:]
        ).encode("utf-8")

    def _document_segment(
        self,
        payload: bytes,
    ) -> tuple[str, int, int, str]:
        """Return the non-first referred document selected by exact number."""
        text = payload.decode("utf-8")
        document_marker = f"<Nb>{self.second_document_number}</Nb>"
        if text.count(document_marker) != 1:
            raise AssertionError(
                "second referred-document number marker must occur exactly once"
            )
        marker_index = text.index(document_marker)
        document_start = text.rfind("<RfrdDocInf>", 0, marker_index)
        document_end_start = text.find("</RfrdDocInf>", marker_index)
        if document_start < 0 or document_end_start < 0:
            raise AssertionError(
                "second referred-document boundaries must be present"
            )
        document_end = document_end_start + len("</RfrdDocInf>")
        return (
            text,
            document_start,
            document_end,
            text[document_start:document_end],
        )

    def _outer_type_segment(
        self,
        document_segment: str,
    ) -> tuple[int, int, str]:
        """Return only the second document's outer Tp, excluding line Id/Tp nodes."""
        type_markup = self.proprietary_contract._coded_type_markup(
            self.document_type_code
        )
        if document_segment.count(type_markup) != 1:
            raise AssertionError(
                "outer coded document type marker must occur exactly once"
            )
        marker_index = document_segment.index(type_markup)
        type_start = document_segment.rfind("<Tp>", 0, marker_index)
        type_end_start = document_segment.find("</Tp>", marker_index)
        if type_start < 0 or type_end_start < 0:
            raise AssertionError("outer referred-document type boundaries must exist")
        type_end = type_end_start + len("</Tp>")
        return type_start, type_end, document_segment[type_start:type_end]


if __name__ == "__main__":
    unittest.main()
