"""REDs for source-faithful absence of line Discount Applied Amount evidence."""

from __future__ import annotations

import hashlib
import re
import unittest
from copy import deepcopy
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_contraction_evidence_red
    as document_contraction_contract,
)

_PARENT_TEST = (
    document_contraction_contract.
    BankStatementStructuredReferredDocumentContractionEvidenceRedTests
)
_RLS_OWNER = document_contraction_contract._RLS_OWNER
_CORRECTION_ERROR = document_contraction_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_DISCOUNT_KEY = "discount_applied_amounts"
_LINE1_APDS = {"type_code": "APDS", "amount": "300", "currency_code": "KRW"}
_LINE2_APDS = {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}


class BankStatementStructuredLineDiscountAbsenceEvidenceRedTests(unittest.TestCase):
    """Preserve zero Discount Applied Amount population on one source line."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL referred-document contraction fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only line-two APDS while retaining its direct Amount group."""
        self.parent = _PARENT_TEST(
            "test_document_contraction_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # #127's contracted source contains one retained CINV document with the
        # canonical two-line population. Keep that source intact except for the
        # second line's sole APDS member so zero cardinality is causal.
        self.base_payload = self.parent.changed_payload
        self.base_statement = self.parent.changed_statement
        self.base_projection = deepcopy(self.parent.changed_projection)

        base_lines = self._line_details(self.base_projection)
        if [line.get("line_number") for line in base_lines] != [
            "Stockitem1",
            "Stockitem2",
        ]:
            raise AssertionError(
                "retained referred document must expose Stockitem1 then Stockitem2"
            )
        if base_lines[0].get(_DISCOUNT_KEY) != [_LINE1_APDS]:
            raise AssertionError("Stockitem1 must retain APDS / 300 KRW")
        if base_lines[1].get(_DISCOUNT_KEY) != [_LINE2_APDS]:
            raise AssertionError("Stockitem2 must begin with exactly APDS / 100 KRW")
        if (
            "due_payable_amount" not in base_lines[1]
            or "remitted_amount" not in base_lines[1]
        ):
            raise AssertionError(
                "Stockitem2 Amount must remain populated outside "
                "Discount Applied Amount"
            )

        self.first_line_projection = deepcopy(base_lines[0])
        self.second_line_without_discount = deepcopy(base_lines[1])
        self.second_line_without_discount.pop(_DISCOUNT_KEY)

        (
            self.changed_payload,
            self.removed_discount_block,
            self.removed_discount_offset,
        ) = self._remove_second_line_discount(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_second_line_discount()

        if self._restore_second_line_discount(
            self.changed_payload,
            self.removed_discount_block,
            self.removed_discount_offset,
        ) != self.base_payload:
            raise AssertionError(
                "exact removed APDS bytes must restore the one-discount source"
            )

    def test_line_discount_absence_is_material_to_each_evidence_hash(self) -> None:
        """Bind one-to-zero APDS contraction to raw and canonical evidence identity."""
        self.assertEqual(
            self._restore_second_line_discount(
                self.changed_payload,
                self.removed_discount_block,
                self.removed_discount_offset,
            ),
            self.base_payload,
        )
        _PARENT_TEST._assert_non_structured_normalization_unchanged(self)

        self.assertEqual(
            self.base_statement.source_artifact_hash,
            "sha256:" + hashlib.sha256(self.base_payload).hexdigest(),
        )
        self.assertEqual(
            self.changed_statement.source_artifact_hash,
            "sha256:" + hashlib.sha256(self.changed_payload).hexdigest(),
        )
        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.changed_statement.source_artifact_hash,
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

    def test_discount_present_baseline_reads_back_before_absence(self) -> None:
        """Persist APDS before contraction so unconditional loss cannot pass."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-discount-absence-present-baseline",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        self.assertEqual(
            self.line_contract.store._artifacts,
            {self.base_statement.source_artifact_hash: self.base_payload},
        )
        statement, entries = self.parent._lookup(
            str(accepted["bank_statement_record_id"]),
            self.base_statement,
        )
        self.parent._assert_statement_hashes(statement, self.base_statement)
        self._assert_primary_entry(
            entries[0],
            self.base_statement.entries[0],
            self.base_projection,
            expect_second_discount=True,
        )
        self.parent._assert_sibling(entries[1], self.base_statement.entries[1])

    def test_rejected_discount_absence_leaves_no_evidence_residue(self) -> None:
        """Reject APDS removal under tenant RLS without relational or raw residue."""
        runtime_url = _RLS_OWNER._restricted_bank_statement_runtime_url(self)
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-discount-absence-base",
            ),
            runtime_url,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        before_rows = _RLS_OWNER._tenant_statement_rows(self, runtime_url)
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
                    "line-discount-absence-changed",
                ),
                runtime_url,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(
            _RLS_OWNER._tenant_statement_rows(self, runtime_url),
            before_rows,
        )
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def test_discount_absence_reads_back_without_sibling_copy(self) -> None:
        """Persist zero APDS on Stockitem2 while Stockitem1 retains its own discount."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-discount-absence-lookup",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        self.assertEqual(
            self.line_contract.store._artifacts,
            {self.changed_statement.source_artifact_hash: self.changed_payload},
        )
        statement, entries = self.parent._lookup(
            str(accepted["bank_statement_record_id"]),
            self.changed_statement,
        )
        self.parent._assert_statement_hashes(statement, self.changed_statement)
        self._assert_primary_entry(
            entries[0],
            self.changed_statement.entries[0],
            self.changed_projection,
            expect_second_discount=False,
        )
        self.parent._assert_sibling(entries[1], self.changed_statement.entries[1])

    def _projection_without_second_line_discount(self) -> list[dict[str, object]]:
        """Remove only Stockitem2 discount key from the expected ordered projection."""
        projection = deepcopy(self.base_projection)
        lines = self._line_details(projection)
        if lines[0] != self.first_line_projection:
            raise AssertionError("Stockitem1 projection must remain unchanged")
        discounts = lines[1].pop(_DISCOUNT_KEY, None)
        if discounts != [_LINE2_APDS]:
            raise AssertionError("projection must remove exact Stockitem2 APDS / 100")
        if lines[1] != self.second_line_without_discount:
            raise AssertionError(
                "non-discount Stockitem2 evidence must remain unchanged"
            )
        return projection

    def _remove_second_line_discount(
        self,
        payload: bytes,
    ) -> tuple[bytes, str, int]:
        """Remove exact Stockitem2 APDS block and retain its source offset."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self._second_line_segment(text)
        block_start, block_end = self._discount_block_bounds(segment)
        removed = segment[block_start:block_end]
        if removed.count("<DscntApldAmt>") != 1:
            raise AssertionError("target must contain exactly one discount member")
        if removed.count("</DscntApldAmt>") != 1:
            raise AssertionError("target discount member must close exactly once")
        if "<Cd>APDS</Cd>" not in removed:
            raise AssertionError("target discount must retain APDS type")
        if '<Amt Ccy="KRW">100.00</Amt>' not in removed:
            raise AssertionError("target discount must retain exact 100.00 KRW")
        changed_segment = segment[:block_start] + segment[block_end:]
        changed = (
            text[:segment_start] + changed_segment + text[segment_end:]
        ).encode("utf-8")

        changed_segment, _, _ = self._second_line_segment(changed.decode("utf-8"))
        if "<DscntApldAmt>" in changed_segment:
            raise AssertionError(
                "Stockitem2 must contain zero discounts after contraction"
            )
        if "<Amt>" not in changed_segment or "</Amt>" not in changed_segment:
            raise AssertionError(
                "Stockitem2 direct Amount group must survive discount removal"
            )
        if "<DuePyblAmt" not in changed_segment or "<RmtdAmt" not in changed_segment:
            raise AssertionError(
                "Due Payable and Remitted Amount must keep direct Amount populated"
            )
        return changed, removed, segment_start + block_start

    def _restore_second_line_discount(
        self,
        payload: bytes,
        removed: str,
        offset: int,
    ) -> bytes:
        """Reinsert exact APDS bytes at the original source offset."""
        text = payload.decode("utf-8")
        if "<Cd>APDS</Cd>" in self._second_line_segment(text)[0]:
            raise AssertionError("contracted Stockitem2 must not already contain APDS")
        if not 0 <= offset <= len(text):
            raise AssertionError(
                "removed discount offset must remain within source bounds"
            )
        restored = (text[:offset] + removed + text[offset:]).encode("utf-8")
        segment, _, _ = self._second_line_segment(restored.decode("utf-8"))
        block_start, block_end = self._discount_block_bounds(segment)
        if segment[block_start:block_end] != removed:
            raise AssertionError("restoration must recover exact APDS source bytes")
        return restored

    def _second_line_segment(self, text: str) -> tuple[str, int, int]:
        """Return the exact Stockitem2 LineDtls segment and source offsets."""
        marker = "<Nb>Stockitem2</Nb>"
        if text.count(marker) != 1:
            raise AssertionError("Stockitem2 number marker must occur exactly once")
        marker_index = text.index(marker)
        start = text.rfind("<LineDtls>", 0, marker_index)
        end_start = text.find("</LineDtls>", marker_index)
        if start < 0 or end_start < 0:
            raise AssertionError("Stockitem2 LineDtls boundaries must exist")
        end = end_start + len("</LineDtls>")
        segment = text[start:end]
        if segment.count("<LineDtls>") != 1 or segment.count("</LineDtls>") != 1:
            raise AssertionError("target must be exactly one complete LineDtls block")
        if "Stockitem1" in segment:
            raise AssertionError("target segment must not include Stockitem1")
        return segment, start, end

    @staticmethod
    def _direct_amount_bounds(lines: list[str]) -> tuple[int, int]:
        """Pair the direct line Amount opener with its same-indent closer."""
        starts = [index for index, line in enumerate(lines) if line.strip() == "<Amt>"]
        if len(starts) != 1:
            raise AssertionError(
                "Stockitem2 must contain exactly one direct Amount group"
            )
        start = starts[0]
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        ends = [
            index
            for index in range(start + 1, len(lines))
            if lines[index].strip() == "</Amt>"
            and lines[index][
                : len(lines[index]) - len(lines[index].lstrip())
            ]
            == indent
        ]
        if len(ends) != 1:
            raise AssertionError("direct Amount must have one same-indent closer")
        return start, ends[0]

    def _discount_block_bounds(self, segment: str) -> tuple[int, int]:
        """Locate the sole direct discount block inside Stockitem2 Amount."""
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self._direct_amount_bounds(lines)
        amount_start_offset = sum(len(line) for line in lines[: amount_start + 1])
        amount_end_offset = sum(len(line) for line in lines[:amount_end])

        matches = list(
            re.finditer(
                r"[ \t]*<DscntApldAmt>.*?</DscntApldAmt>(?:\r?\n)?",
                segment,
                re.DOTALL,
            )
        )
        matches = [
            match
            for match in matches
            if match.start() >= amount_start_offset and match.end() <= amount_end_offset
        ]
        if len(matches) != 1:
            raise AssertionError(
                "Stockitem2 direct Amount must contain exactly one discount block"
            )
        return matches[0].start(), matches[0].end()

    @staticmethod
    def _line_details(
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Return the sole retained document's ordered line-detail population."""
        if len(projection) != 1:
            raise AssertionError("discount absence RED requires one referred document")
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError(
                "retained document must expose exactly two line details"
            )
        if not all(isinstance(line, dict) for line in lines):
            raise AssertionError("every line detail projection must be a mapping")
        return lines

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        *,
        expect_second_discount: bool,
    ) -> None:
        """Assert exact projection, hashes, and 25000-KRW transaction truth."""
        persisted_details = persisted_entry["entry_details"]
        self.assertEqual(len(persisted_details), len(expected_entry.entry_details))
        persisted_detail = persisted_details[0]
        expected_detail = expected_entry.entry_details[0]

        self.assertEqual(
            persisted_entry["source_entry_hash"],
            expected_entry.source_entry_hash,
        )
        self.assertEqual(
            persisted_detail["source_detail_hash"],
            expected_detail.source_detail_hash,
        )
        self.assertEqual(
            persisted_detail[_STRUCTURED_EVIDENCE_KEY],
            expected_projection,
        )

        lines = self._line_details(persisted_detail[_STRUCTURED_EVIDENCE_KEY])
        self.assertEqual(lines[0], self.first_line_projection)
        self.assertEqual(lines[0].get(_DISCOUNT_KEY), [_LINE1_APDS])
        if expect_second_discount:
            self.assertEqual(lines[1].get(_DISCOUNT_KEY), [_LINE2_APDS])
        else:
            self.assertNotIn(_DISCOUNT_KEY, lines[1])
            self.assertEqual(lines[1], self.second_line_without_discount)
        self.assertIn("due_payable_amount", lines[1])
        self.assertIn("remitted_amount", lines[1])

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


if __name__ == "__main__":
    unittest.main()
