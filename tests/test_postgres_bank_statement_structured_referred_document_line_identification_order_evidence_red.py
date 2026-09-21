"""REDs for source-order preservation across repeated referred-document line Id members."""

from __future__ import annotations

import hashlib
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
    test_postgres_bank_statement_structured_referred_document_line_identification_cardinality_evidence_red
    as cardinality_contract,
)

_PARENT_TEST = (
    cardinality_contract.BankStatementStructuredLineIdentificationCardinalityEvidenceRedTests
)
_CORRECTION_ERROR = cardinality_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineIdentificationOrderEvidenceRedTests(unittest.TestCase):
    """Retain the bank source order of every repeated LineDtls/Id member."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL repeated-identification fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare the same two Id members in opposite source order."""
        self.parent = _PARENT_TEST(
            "test_repeated_line_identification_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_payload = self.parent.changed_payload
        self.changed_payload = self._swap_second_line_identifications(self.base_payload)
        self.base_statement = self.parent.changed_statement
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.changed_projection)
        self.changed_projection = self._projection_with_reversed_identification_order()

    def test_repeated_line_identification_order_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind an Id-order-only source change to raw, detail, entry, and statement identity."""
        self.assertEqual(
            self._swap_second_line_identifications(self.changed_payload),
            self.base_payload,
        )
        self._assert_non_structured_normalization_unchanged()

        expected_base_artifact_hash = (
            "sha256:" + hashlib.sha256(self.base_payload).hexdigest()
        )
        expected_changed_artifact_hash = (
            "sha256:" + hashlib.sha256(self.changed_payload).hexdigest()
        )
        self.assertEqual(
            self.base_statement.source_artifact_hash,
            expected_base_artifact_hash,
        )
        self.assertEqual(
            self.changed_statement.source_artifact_hash,
            expected_changed_artifact_hash,
        )
        self.assertNotEqual(
            expected_base_artifact_hash,
            expected_changed_artifact_hash,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
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
        self.line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        self.line_contract._assert_exact_transaction_amount(
            changed_entry,
            changed_detail,
        )

    def test_rejected_line_identification_reorder_preserves_all_accepted_evidence(
        self,
    ) -> None:
        """Reject an order-only replay without relational or immutable-artifact residue."""
        runtime_url = self.parent._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-identification-order-base",
            ),
            runtime_url,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])

        before_rows = self.parent._tenant_statement_rows(runtime_url)
        expected_artifacts = {
            self.base_statement.source_artifact_hash: self.base_payload,
        }
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertTrue(before_rows["bank_statement_artifact"])
        self.assertTrue(before_rows["bank_statement_entry"])
        self.assertTrue(before_rows["bank_statement_entry_detail"])
        self.assertTrue(
            any(
                str(row[0]) == record_id
                for row in before_rows["bank_statement_record"]
            )
        )

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "line-identification-order-changed",
                ),
                runtime_url,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(self.parent._tenant_statement_rows(runtime_url), before_rows)
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def test_buyer_read_retains_reordered_identifications_and_first_source_scalar(
        self,
    ) -> None:
        """Expose exact reversed Id order and keep legacy scalar fields on the first Id."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-identification-order-lookup",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertEqual(
            self.line_contract.store._artifacts,
            {self.changed_statement.source_artifact_hash: self.changed_payload},
        )
        record_id = str(accepted["bank_statement_record_id"])
        statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]
        changed_entry = self.changed_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]

        self.assertEqual(
            statement["source_artifact_hash"],
            self.changed_statement.source_artifact_hash,
        )
        self.assertEqual(
            statement["normalized_payload_hash"],
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(entry["source_entry_hash"], changed_entry.source_entry_hash)
        self.assertEqual(detail["source_detail_hash"], changed_detail.source_detail_hash)
        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.changed_projection,
        )

        second_line = detail[_STRUCTURED_EVIDENCE_KEY][0]["line_details"][1]
        self.assertEqual(
            second_line["line_type_code"],
            self.parent.second_identification_type_code,
        )
        self.assertEqual(
            second_line["line_number"],
            self.parent.second_identification_number,
        )
        self.assertEqual(
            second_line["related_date"],
            self.parent.second_identification_related_date,
        )
        self.assertEqual(
            second_line["line_identifications"],
            [
                {
                    "type_code": self.parent.second_identification_type_code,
                    "number": self.parent.second_identification_number,
                    "related_date": self.parent.second_identification_related_date,
                },
                {
                    "type_code": "SKNB",
                    "number": self.line_contract.second_line_number,
                    "related_date": self.line_contract.line_related_date,
                },
            ],
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_with_reversed_identification_order(
        self,
    ) -> list[dict[str, object]]:
        """Reverse only the complete Id population and derive legacy scalars from source-first."""
        projection = deepcopy(self.base_projection)
        if len(projection) != 1:
            raise AssertionError("order RED requires one referred document")
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("order RED requires exactly two source lines")
        second_line = lines[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second source line projection must be a mapping")
        identifications = second_line.get("line_identifications")
        if not isinstance(identifications, list) or len(identifications) != 2:
            raise AssertionError("order RED requires exactly two line identifications")

        first = {
            "type_code": "SKNB",
            "number": self.line_contract.second_line_number,
            "related_date": self.line_contract.line_related_date,
        }
        second = {
            "type_code": self.parent.second_identification_type_code,
            "number": self.parent.second_identification_number,
            "related_date": self.parent.second_identification_related_date,
        }
        if identifications != [first, second]:
            raise AssertionError("parent repeated-identification order must remain canonical")

        reordered = [second, first]
        second_line["line_identifications"] = reordered
        second_line["line_type_code"] = second["type_code"]
        second_line["line_number"] = second["number"]
        second_line["related_date"] = second["related_date"]
        return projection

    def _swap_second_line_identifications(self, payload: bytes) -> bytes:
        """Swap exactly two complete Id blocks without reconstructing either member."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self.parent._second_line_segment(text)
        starts: list[int] = []
        cursor = 0
        while True:
            start = line_segment.find("<Id>", cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len("<Id>")
        if len(starts) != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("order RED requires exactly two complete Id members")

        first_start, second_start = starts
        first_end = line_segment.find("</Id>", first_start)
        second_end = line_segment.find("</Id>", second_start)
        if first_end < 0 or second_end < 0:
            raise AssertionError("both Id closing tags must be present")
        first_end += len("</Id>")
        second_end += len("</Id>")
        if not first_start < first_end <= second_start < second_end:
            raise AssertionError("Id members must be non-overlapping and source ordered")

        first_block = line_segment[first_start:first_end]
        second_block = line_segment[second_start:second_end]
        identities = {
            self._identification_identity(first_block),
            self._identification_identity(second_block),
        }
        expected = {
            (
                "SKNB",
                self.line_contract.second_line_number,
                self.line_contract.line_related_date,
            ),
            (
                self.parent.second_identification_type_code,
                self.parent.second_identification_number,
                self.parent.second_identification_related_date,
            ),
        }
        if identities != expected:
            raise AssertionError("Id population must contain the exact canonical two members")

        separator = line_segment[first_end:second_start]
        swapped_line = (
            line_segment[:first_start]
            + second_block
            + separator
            + first_block
            + line_segment[second_end:]
        )
        return (text[:line_start] + swapped_line + text[line_end:]).encode("utf-8")

    def _identification_identity(self, block: str) -> tuple[str, str, str]:
        """Read the exact fixture identity so the swap cannot hide member mutation."""
        candidates = (
            (
                "SKNB",
                self.line_contract.second_line_number,
                self.line_contract.line_related_date,
            ),
            (
                self.parent.second_identification_type_code,
                self.parent.second_identification_number,
                self.parent.second_identification_related_date,
            ),
        )
        matches = [
            identity
            for identity in candidates
            if f"<Cd>{identity[0]}</Cd>" in block
            and f"<Nb>{identity[1]}</Nb>" in block
            and f"<RltdDt>{identity[2]}</RltdDt>" in block
        ]
        if len(matches) != 1:
            raise AssertionError("each Id block must match exactly one complete fixture identity")
        return matches[0]

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove the source-order delta cannot hide collateral normalized-field drift."""
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
                tuple(getattr(base_entry, field) for field in entry_fields),
                tuple(getattr(changed_entry, field) for field in entry_fields),
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
                    tuple(getattr(base_detail, field) for field in detail_fields),
                    tuple(getattr(changed_detail, field) for field in detail_fields),
                )


if __name__ == "__main__":
    unittest.main()
