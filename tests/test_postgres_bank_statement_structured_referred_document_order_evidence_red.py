"""REDs for repeated referred-document source-order preservation."""

from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    lookup_bank_statement,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_type_issuer_evidence_red
    as type_issuer_contract,
)

_PARENT_TEST = (
    type_issuer_contract.
    BankStatementStructuredReferredDocumentTypeIssuerEvidenceRedTests
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredReferredDocumentOrderEvidenceRedTests(unittest.TestCase):
    """Retain repeated RfrdDocInf members in their exact source order."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare two statements with identical documents in opposite source order."""
        self.parent = _PARENT_TEST(
            "test_document_type_issuer_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.first_document_number = self.parent.first_document_number
        self.second_document_number = self.parent.second_document_number
        self.base_payload = self.parent.changed_payload
        self.changed_payload = self._swap_referred_documents(self.base_payload)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(
            self.parent._structured_projection(
                self.parent.changed_document_type_issuer
            )
        )
        self.changed_projection = self._reverse_projection(self.base_projection)

    def test_referred_document_source_order_is_material_to_each_canonical_evidence_hash(
        self,
    ) -> None:
        """Bind an order-only repeated-document change to every evidence hash."""
        self.assertEqual(
            self._swap_referred_documents(self.changed_payload),
            self.base_payload,
        )
        self.assertEqual(
            [document["document_number"] for document in self.base_projection],
            [self.first_document_number, self.second_document_number],
        )
        self.assertEqual(
            [document["document_number"] for document in self.changed_projection],
            [self.second_document_number, self.first_document_number],
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

    def test_changed_referred_document_order_fails_closed_for_same_statement_identity(
        self,
    ) -> None:
        """Reject reordered evidence without mutating the accepted statement."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "referred-document-order-base",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        before_statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )
        before_entries = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "referred-document-order-changed",
                ),
                posting.DATABASE_URL,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        after_statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )
        after_entries = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )
        self.assertEqual(after_statement, before_statement)
        self.assertEqual(after_entries, before_entries)
        self.assertEqual(
            after_statement["source_artifact_hash"],
            self.base_statement.source_artifact_hash,
        )
        self.assertEqual(
            after_statement["normalized_payload_hash"],
            self.base_statement.normalized_payload_hash,
        )
        self.assertNotEqual(
            after_statement["source_artifact_hash"],
            self.changed_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            after_statement["normalized_payload_hash"],
            self.changed_statement.normalized_payload_hash,
        )

        persisted_entries = after_entries["bank_statement_entries"]
        self.assertEqual(len(persisted_entries), len(self.base_statement.entries))
        persisted_entry = persisted_entries[0]
        persisted_detail = persisted_entry["entry_details"][0]
        self.assertEqual(
            persisted_detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.base_projection,
        )
        self.assertEqual(
            [
                item["document_number"]
                for item in persisted_detail[_STRUCTURED_EVIDENCE_KEY]
            ],
            [self.first_document_number, self.second_document_number],
        )
        self.assertEqual(
            Decimal(str(persisted_entry["entry_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(persisted_entry["entry_currency_code"], "KRW")
        self.assertEqual(
            Decimal(str(persisted_detail["detail_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(persisted_detail["detail_currency_code"], "KRW")

    def test_buyer_read_retains_referred_document_source_order(self) -> None:
        """Buyer reads return repeated referred documents in changed source order."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "referred-document-order-lookup",
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
            [item["document_number"] for item in detail[_STRUCTURED_EVIDENCE_KEY]],
            [self.second_document_number, self.first_document_number],
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
        """Prove order-only source change cannot hide scalar normalization changes."""
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

    def _swap_referred_documents(self, payload: bytes) -> bytes:
        """Swap the two exact RfrdDocInf blocks while preserving block bytes."""
        text = payload.decode("utf-8")
        first_bounds = self._document_bounds(text, self.first_document_number)
        second_bounds = self._document_bounds(text, self.second_document_number)
        ordered = sorted(
            (first_bounds, second_bounds),
            key=lambda bounds: bounds[0],
        )
        (left_start, left_end), (right_start, right_end) = ordered
        if left_end > right_start:
            raise AssertionError("referred-document blocks must not overlap")
        left_block = text[left_start:left_end]
        right_block = text[right_start:right_end]
        between = text[left_end:right_start]
        return (
            text[:left_start]
            + right_block
            + between
            + left_block
            + text[right_end:]
        ).encode("utf-8")

    @staticmethod
    def _reverse_projection(
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Reverse exactly two complete referred-document projections."""
        if len(projection) != 2:
            raise AssertionError(
                "order RED requires exactly two referred documents"
            )
        return [deepcopy(projection[1]), deepcopy(projection[0])]

    @staticmethod
    def _document_bounds(text: str, document_number: str) -> tuple[int, int]:
        """Return exact RfrdDocInf boundaries for one unique document number."""
        marker = f"<Nb>{document_number}</Nb>"
        if text.count(marker) != 1:
            raise AssertionError(
                "referred-document number marker must occur exactly once"
            )
        marker_index = text.index(marker)
        start = text.rfind("<RfrdDocInf>", 0, marker_index)
        end_start = text.find("</RfrdDocInf>", marker_index)
        if start < 0 or end_start < 0:
            raise AssertionError("referred-document boundaries must be present")
        return start, end_start + len("</RfrdDocInf>")


if __name__ == "__main__":
    unittest.main()
