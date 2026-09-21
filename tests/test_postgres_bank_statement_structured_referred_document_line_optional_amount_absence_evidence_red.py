"""REDs for optional referred-document line Amount-group absence evidence."""

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
    test_postgres_bank_statement_structured_referred_document_line_optional_description_absence_evidence_red
    as optional_description_contract,
)

_PARENT_TEST = (
    optional_description_contract.
    BankStatementStructuredLineOptionalDescriptionAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = optional_description_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = optional_description_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_AMOUNT_EVIDENCE_KEYS = (
    "due_payable_amount",
    "discount_applied_amounts",
    "credit_note_amount",
    "tax_amounts",
    "adjustments",
    "remitted_amount",
)
_REQUIRED_SECOND_LINE_AMOUNT_KEYS = (
    "due_payable_amount",
    "discount_applied_amounts",
    "remitted_amount",
)


class BankStatementStructuredLineOptionalAmountAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve complete Amount-group absence on the exact source LineDtls member."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Description-absence fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only line two Amount while retaining all inherited omissions."""
        self.parent = _PARENT_TEST(
            "test_optional_description_absence_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # Compose on #115's changed source. The second line therefore still omits
        # Description and its first repeated Identification still omits Type.
        self.base_payload = self.parent.changed_payload
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.changed_projection)
        first_line, second_line = self._line_details(self.base_projection)
        self.second_line_number = str(second_line["line_number"])
        self.first_line_amount_evidence = {
            key: deepcopy(first_line[key])
            for key in _AMOUNT_EVIDENCE_KEYS
            if key in first_line
        }
        self.second_line_amount_evidence = {
            key: deepcopy(second_line[key])
            for key in _AMOUNT_EVIDENCE_KEYS
            if key in second_line
        }
        missing_required = [
            key
            for key in _REQUIRED_SECOND_LINE_AMOUNT_KEYS
            if key not in self.second_line_amount_evidence
        ]
        if missing_required:
            raise AssertionError(
                "second source line must retain the populated Amount evidence used by "
                "this fixture: " + ", ".join(missing_required)
            )
        self.amount_block = self._extract_second_line_amount_block(self.base_payload)

        self.changed_payload = self._remove_second_line_amount(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_second_line_amount()

    def test_optional_amount_absence_is_material_to_each_evidence_hash(self) -> None:
        """Bind one complete source-line Amount omission through canonical evidence identity."""
        self.assertEqual(
            self._restore_second_line_amount(self.changed_payload),
            self.base_payload,
        )
        _NORMALIZATION_PARENT_TEST._assert_non_structured_normalization_unchanged(self)

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
        self.assertNotEqual(expected_base_artifact_hash, expected_changed_artifact_hash)

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
            self.line_contract._expected_detail_hash(base_detail, self.base_projection),
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
        self.line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        self.line_contract._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_rejected_amount_absence_change_leaves_no_evidence_residue(self) -> None:
        """Reject Amount omission without changing relational or raw-source evidence."""
        restricted_owner = self.parent._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-optional-amount-base",
            ),
            runtime_url,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        before_rows = restricted_owner._tenant_statement_rows(runtime_url)
        expected_artifacts = {
            self.base_statement.source_artifact_hash: self.base_payload,
        }
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
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
                    "line-optional-amount-absent",
                ),
                runtime_url,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(restricted_owner._tenant_statement_rows(runtime_url), before_rows)
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def test_buyer_read_keeps_amount_absent_on_exact_source_line(self) -> None:
        """Expose no line-two monetary members without copying the sibling Amount group."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-optional-amount-lookup",
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
        self.assertEqual(detail.get(_STRUCTURED_EVIDENCE_KEY), self.changed_projection)

        first_line, second_line = self._line_details(detail[_STRUCTURED_EVIDENCE_KEY])
        for key, expected in self.first_line_amount_evidence.items():
            self.assertEqual(first_line[key], expected)
        for key in _AMOUNT_EVIDENCE_KEYS:
            self.assertNotIn(key, second_line)

        # The direct parent remains live: line two still omits Description rather
        # than receiving the sibling's text while its exact line identity survives.
        self.assertEqual(first_line["description"], self.parent.first_line_description)
        self.assertNotIn("description", second_line)
        self.assertEqual(second_line["line_number"], self.second_line_number)

        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_second_line_amount(self) -> list[dict[str, object]]:
        """Remove the populated Amount evidence from line two's canonical projection."""
        projection = deepcopy(self.base_projection)
        first_line, second_line = self._line_details(projection)
        for key, expected in self.second_line_amount_evidence.items():
            if second_line.get(key) != expected:
                raise AssertionError(
                    "populated line-two Amount evidence must remain exact before omission"
                )
            second_line.pop(key)
        for key in _AMOUNT_EVIDENCE_KEYS:
            if key in second_line:
                raise AssertionError(
                    "whole Amount omission must remove every Amount-derived projection key"
                )
        for key, expected in self.first_line_amount_evidence.items():
            if first_line.get(key) != expected:
                raise AssertionError("first source line Amount evidence must remain unchanged")
        return projection

    def _extract_second_line_amount_block(self, payload: bytes) -> str:
        """Return exact bytes from the direct line Amount opener through its trailing gap."""
        text = payload.decode("utf-8")
        segment, _, _ = self._second_line_segment(text)
        lines = segment.splitlines(keepends=True)
        open_indexes = [
            index for index, line in enumerate(lines) if line.strip() == "<Amt>"
        ]
        line_close_indexes = [
            index for index, line in enumerate(lines) if line.strip() == "</LineDtls>"
        ]
        if len(open_indexes) != 1:
            raise AssertionError("second source line requires one direct Amount opener")
        if len(line_close_indexes) != 1:
            raise AssertionError("second source line requires one LineDtls closing tag")
        amount_open = open_indexes[0]
        direct_indent = lines[amount_open][
            : len(lines[amount_open]) - len(lines[amount_open].lstrip())
        ]
        close_indexes = [
            index
            for index, line in enumerate(lines[amount_open + 1 :], amount_open + 1)
            if line.strip() == "</Amt>"
            and line[: len(line) - len(line.lstrip())] == direct_indent
        ]
        if len(close_indexes) != 1:
            raise AssertionError("second source line requires one direct Amount closer")
        amount_close = close_indexes[0]
        line_close = line_close_indexes[0]
        if not amount_open < amount_close < line_close:
            raise AssertionError("direct Amount group must close before LineDtls")
        if any(line.strip() for line in lines[amount_close + 1 : line_close]):
            raise AssertionError("Amount must remain the final populated LineDtls child")
        block = "".join(lines[amount_open:line_close])
        if not block or "<DuePyblAmt" not in block or "<RmtdAmt" not in block:
            raise AssertionError("Amount block must retain canonical monetary evidence")
        return block

    def _remove_second_line_amount(self, payload: bytes) -> bytes:
        """Remove only the exact direct Amount block from the second LineDtls member."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self._second_line_segment(text)
        if segment.count(self.amount_block) != 1:
            raise AssertionError("second source line must contain its exact Amount block once")
        changed_segment = segment.replace(self.amount_block, "", 1)
        if any(
            line.strip() == "<Amt>"
            for line in changed_segment.splitlines(keepends=True)
        ):
            raise AssertionError("Amount-absence fixture must omit the direct Amount group")
        return (text[:segment_start] + changed_segment + text[segment_end:]).encode(
            "utf-8"
        )

    def _restore_second_line_amount(self, payload: bytes) -> bytes:
        """Restore the exact removed Amount bytes immediately before LineDtls closes."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self._second_line_segment(text)
        lines = segment.splitlines(keepends=True)
        if any(line.strip() == "<Amt>" for line in lines):
            raise AssertionError("Amount-absence fixture must not already contain Amount")
        close_indexes = [
            index for index, line in enumerate(lines) if line.strip() == "</LineDtls>"
        ]
        if len(close_indexes) != 1:
            raise AssertionError("second source line requires one LineDtls closing tag")
        close_index = close_indexes[0]
        restored_segment = "".join(
            lines[:close_index]
            + [self.amount_block]
            + lines[close_index:]
        )
        return (text[:segment_start] + restored_segment + text[segment_end:]).encode(
            "utf-8"
        )

    def _second_line_segment(self, text: str) -> tuple[str, int, int]:
        """Return the unique LineDtls member containing the canonical line-two number."""
        marker = f"<Nb>{self.second_line_number}</Nb>"
        if text.count(marker) != 1:
            raise AssertionError("second line scalar number marker must occur exactly once")
        marker_index = text.index(marker)
        start = text.rfind("<LineDtls>", 0, marker_index)
        close_start = text.find("</LineDtls>", marker_index)
        if start < 0 or close_start < 0:
            raise AssertionError("second source line boundaries must surround its number")
        end = close_start + len("</LineDtls>")
        if end < len(text) and text[end] == "\n":
            end += 1
        return text[start:end], start, end

    @staticmethod
    def _line_details(
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return the exact two source-ordered LineDtls mappings from one document."""
        if len(projection) != 1:
            raise AssertionError("Amount-absence RED requires one referred document")
        line_details = projection[0].get("line_details")
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("Amount-absence RED requires two source lines")
        if not all(isinstance(line, dict) for line in line_details):
            raise AssertionError("every LineDtls projection must be a mapping")
        return line_details[0], line_details[1]


if __name__ == "__main__":
    unittest.main()
