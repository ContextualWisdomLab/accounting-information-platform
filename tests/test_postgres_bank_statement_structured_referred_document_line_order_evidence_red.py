"""REDs for referred-document line source-order preservation."""

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
    test_postgres_bank_statement_structured_referred_document_order_evidence_red
    as document_order_contract,
)

_PARENT_TEST = (
    document_order_contract.
    BankStatementStructuredReferredDocumentOrderEvidenceRedTests
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredReferredDocumentLineOrderEvidenceRedTests(unittest.TestCase):
    """Retain repeated LineDtls members in their exact source order."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare one referred document with the same two lines in opposite order."""
        self.parent = _PARENT_TEST(
            "test_referred_document_source_order_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.document_number = self.parent.first_document_number
        self.base_payload = self.parent.base_payload
        self.changed_payload = self._swap_first_document_lines(self.base_payload)
        self.base_statement = self.parent.base_statement
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.base_projection)
        self.changed_projection = self._reverse_first_document_lines(
            self.base_projection
        )

        first_document = self.base_projection[0]
        lines = first_document.get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError(
                "line-order RED requires exactly two lines on the first referred document"
            )
        self.first_line_number = str(lines[0]["line_number"])
        self.second_line_number = str(lines[1]["line_number"])

    def test_referred_document_line_source_order_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind a line-order-only source change to detail, entry, and statement identity."""
        self.assertEqual(
            self._swap_first_document_lines(self.changed_payload),
            self.base_payload,
        )
        self.assertEqual(
            self._line_numbers(self.base_projection),
            [self.first_line_number, self.second_line_number],
        )
        self.assertEqual(
            self._line_numbers(self.changed_projection),
            [self.second_line_number, self.first_line_number],
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]

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
            self.line_contract._expected_detail_hash(
                base_detail,
                self.base_projection,
            ),
        )
        self.assertEqual(
            changed_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(
                changed_detail,
                self.changed_projection,
            ),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(
                base_entry,
                {1: self.base_projection},
            ),
        )
        self.assertEqual(
            changed_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(
                changed_entry,
                {1: self.changed_projection},
            ),
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.base_statement,
                {(1, 1): self.base_projection},
            ),
        )
        self.assertEqual(
            self.changed_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.changed_statement,
                {(1, 1): self.changed_projection},
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

    def test_changed_line_order_fails_closed_without_mutating_accepted_evidence(
        self,
    ) -> None:
        """Reject reordered lines while retaining the already accepted source evidence."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "referred-document-line-order-base",
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
                    "referred-document-line-order-changed",
                ),
                posting.DATABASE_URL,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        persisted = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(
            persisted["source_artifact_hash"],
            self.base_statement.source_artifact_hash,
        )
        self.assertEqual(
            persisted["normalized_payload_hash"],
            self.base_statement.normalized_payload_hash,
        )
        self.assertNotEqual(
            persisted["source_artifact_hash"],
            self.changed_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            persisted["normalized_payload_hash"],
            self.changed_statement.normalized_payload_hash,
        )
        persisted_entry = persisted["bank_statement_entries"][0]
        persisted_detail = persisted_entry["entry_details"][0]
        self.assertEqual(
            persisted_detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.base_projection,
        )
        self.assertEqual(
            self._line_numbers(persisted_detail[_STRUCTURED_EVIDENCE_KEY]),
            [self.first_line_number, self.second_line_number],
        )
        self._assert_exact_persisted_transaction_amount(
            persisted_entry,
            persisted_detail,
        )

    def test_buyer_read_retains_referred_document_line_source_order(self) -> None:
        """Buyer reads return the first document's lines in changed source order."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "referred-document-line-order-lookup",
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
            self.changed_projection,
        )
        self.assertEqual(
            self._line_numbers(detail[_STRUCTURED_EVIDENCE_KEY]),
            [self.second_line_number, self.first_line_number],
        )
        self._assert_exact_persisted_transaction_amount(entry, detail)

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove line-order-only source change cannot hide scalar normalization changes."""
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

    def _swap_first_document_lines(self, payload: bytes) -> bytes:
        """Swap two complete LineDtls blocks inside the first referred document only."""
        text = payload.decode("utf-8")
        document_start, document_end = self.parent._document_bounds(
            text,
            self.document_number,
        )
        document = text[document_start:document_end]
        first_bounds = self._line_bounds(document, self.first_line_number)
        second_bounds = self._line_bounds(document, self.second_line_number)
        ordered = sorted(
            (first_bounds, second_bounds),
            key=lambda bounds: bounds[0],
        )
        (left_start, left_end), (right_start, right_end) = ordered
        if left_end > right_start:
            raise AssertionError("referred-document line blocks must not overlap")
        left_block = document[left_start:left_end]
        right_block = document[right_start:right_end]
        between = document[left_end:right_start]
        swapped = (
            document[:left_start]
            + right_block
            + between
            + left_block
            + document[right_end:]
        )
        return (
            text[:document_start]
            + swapped
            + text[document_end:]
        ).encode("utf-8")

    @staticmethod
    def _line_bounds(document: str, line_number: str) -> tuple[int, int]:
        """Return exact LineDtls boundaries for one unique source line number."""
        marker = f"<Nb>{line_number}</Nb>"
        if document.count(marker) != 1:
            raise AssertionError("line number marker must occur exactly once in the document")
        marker_index = document.index(marker)
        start = document.rfind("<LineDtls>", 0, marker_index)
        end_start = document.find("</LineDtls>", marker_index)
        if start < 0 or end_start < 0:
            raise AssertionError("referred-document line boundaries must be present")
        return start, end_start + len("</LineDtls>")

    @staticmethod
    def _reverse_first_document_lines(
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Reverse exactly two complete lines on the first referred document."""
        changed = deepcopy(projection)
        if not changed:
            raise AssertionError("line-order RED requires a referred document")
        lines = changed[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("line-order RED requires exactly two first-document lines")
        changed[0]["line_details"] = [deepcopy(lines[1]), deepcopy(lines[0])]
        return changed

    @staticmethod
    def _line_numbers(projection: list[dict[str, object]]) -> list[str]:
        """Return ordered first-document line numbers from a structured projection."""
        if not projection:
            raise AssertionError("structured projection must include a referred document")
        lines = projection[0].get("line_details")
        if not isinstance(lines, list):
            raise AssertionError("first referred document must include line details")
        return [str(line["line_number"]) for line in lines]

    @staticmethod
    def _assert_exact_persisted_transaction_amount(
        entry: dict[str, object],
        detail: dict[str, object],
    ) -> None:
        """Keep source line ordering separate from authoritative transaction truth."""
        if Decimal(str(entry["entry_amount"])) != Decimal("25000.00"):
            raise AssertionError("entry transaction amount must remain exactly 25000.00")
        if entry["entry_currency_code"] != "KRW":
            raise AssertionError("entry transaction currency must remain KRW")
        if Decimal(str(detail["detail_amount"])) != Decimal("25000.00"):
            raise AssertionError("detail transaction amount must remain exactly 25000.00")
        if detail["detail_currency_code"] != "KRW":
            raise AssertionError("detail transaction currency must remain KRW")


if __name__ == "__main__":
    unittest.main()
