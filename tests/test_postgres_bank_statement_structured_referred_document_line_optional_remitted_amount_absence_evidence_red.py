"""REDs for optional referred-document line Remitted Amount absence evidence."""

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
    test_postgres_bank_statement_structured_referred_document_line_optional_due_payable_absence_evidence_red
    as due_payable_contract,
)

_PARENT_TEST = (
    due_payable_contract.BankStatementStructuredLineOptionalDuePayableAmountAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = due_payable_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = due_payable_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_TARGET_KEY = "remitted_amount"
_REQUIRED_RETAINED_KEYS = (
    "discount_applied_amounts",
    "credit_note_amount",
    "tax_amounts",
    "adjustments",
)


class BankStatementStructuredLineOptionalRemittedAmountAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve Remitted Amount absence on the exact source LineDtls member."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Due-Payable fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only line-two RmtdAmt while retaining the rest of Amount."""
        self.parent = _PARENT_TEST(
            "test_optional_due_payable_absence_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # Use #117's changed source so DuePyblAmt absence remains live while the
        # direct Amount group and every other populated monetary member survive.
        self.base_payload = self.parent.changed_payload
        self.base_statement = self.parent.changed_statement
        self.base_projection = deepcopy(self.parent.changed_projection)
        first_line, second_line = self._line_details(self.base_projection)
        self.second_line_number = str(second_line["line_number"])
        self.first_line_amount_evidence = {
            key: deepcopy(first_line[key])
            for key in due_payable_contract.optional_amount_contract._AMOUNT_EVIDENCE_KEYS
            if key in first_line
        }
        self.second_line_other_amount_evidence = {
            key: deepcopy(second_line[key])
            for key in due_payable_contract.optional_amount_contract._AMOUNT_EVIDENCE_KEYS
            if key != _TARGET_KEY and key in second_line
        }
        missing_required = [
            key
            for key in _REQUIRED_RETAINED_KEYS
            if key not in self.second_line_other_amount_evidence
        ]
        if missing_required:
            raise AssertionError(
                "second source line must retain populated non-target Amount evidence: "
                + ", ".join(missing_required)
            )
        if "due_payable_amount" in second_line:
            raise AssertionError(
                "parent #117 contract must keep line-two Due Payable Amount absent"
            )
        self.second_line_absent_optional_amount_keys = tuple(
            key
            for key in due_payable_contract.optional_amount_contract._AMOUNT_EVIDENCE_KEYS
            if key != _TARGET_KEY and key not in second_line
        )

        self.remitted_line = self._extract_second_line_remitted_line(self.base_payload)
        self.changed_payload = self._remove_second_line_remitted_amount(
            self.base_payload
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_second_line_remitted()

    def test_optional_remitted_absence_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind one source-line Remitted Amount omission through evidence identity."""
        self.assertEqual(
            self._restore_second_line_remitted_amount(self.changed_payload),
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
        self.line_contract._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_rejected_remitted_absence_change_leaves_no_evidence_residue(
        self,
    ) -> None:
        """Reject RmtdAmt omission without changing relational or raw evidence."""
        restricted_owner = self._ancestor_with(
            self.parent,
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-optional-remitted-base",
            ),
            runtime_url,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        before_rows = restricted_owner._tenant_statement_rows(runtime_url)
        expected_artifacts = deepcopy(self.line_contract.store._artifacts)
        self.assertEqual(
            expected_artifacts,
            {self.base_statement.source_artifact_hash: self.base_payload},
        )
        retained_artifact_hashes = set(expected_artifacts)
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
                    "line-optional-remitted-absent",
                ),
                runtime_url,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(restricted_owner._tenant_statement_rows(runtime_url), before_rows)
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertEqual(
            set(self.line_contract.store._artifacts),
            retained_artifact_hashes,
        )
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def test_buyer_read_keeps_remitted_absent_on_exact_source_line(self) -> None:
        """Expose no line-two Remitted Amount without erasing sibling monetary evidence."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-optional-remitted-lookup",
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

        first_line, second_line = self._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        for key, expected in self.first_line_amount_evidence.items():
            self.assertEqual(first_line[key], expected)
        self.assertNotIn(_TARGET_KEY, second_line)
        for key, expected in self.second_line_other_amount_evidence.items():
            self.assertEqual(second_line[key], expected)
        for key in self.second_line_absent_optional_amount_keys:
            self.assertNotIn(key, second_line)

        # Inherited omission contracts remain live.
        self.assertNotIn("due_payable_amount", second_line)
        self.assertNotIn("description", second_line)
        self.assertEqual(second_line["line_number"], self.second_line_number)

        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_second_line_remitted(
        self,
    ) -> list[dict[str, object]]:
        """Remove only Remitted Amount from line two's canonical projection."""
        projection = deepcopy(self.base_projection)
        first_line, second_line = self._line_details(projection)
        if _TARGET_KEY not in second_line:
            raise AssertionError("second source line must begin with Remitted Amount")
        second_line.pop(_TARGET_KEY)
        for key, expected in self.second_line_other_amount_evidence.items():
            if second_line.get(key) != expected:
                raise AssertionError(
                    "populated non-target line-two Amount evidence must remain unchanged"
                )
        for key in self.second_line_absent_optional_amount_keys:
            if key in second_line:
                raise AssertionError(
                    "Remitted Amount omission must not manufacture absent Amount evidence"
                )
        for key, expected in self.first_line_amount_evidence.items():
            if first_line.get(key) != expected:
                raise AssertionError(
                    "first source line Amount evidence must remain unchanged"
                )
        return projection

    def _line_details(
        self,
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Delegate source-line scoping to the established LineDtls contract."""
        return self.parent.parent._line_details(projection)

    @staticmethod
    def _ancestor_with(
        start: object,
        *attributes: str,
    ) -> object:
        """Find the existing fixture owner without assuming parent-chain depth."""
        current: object | None = start
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            if all(hasattr(current, attribute) for attribute in attributes):
                return current
            current = getattr(current, "parent", None)
        raise AssertionError(
            "fixture ancestry must expose " + ", ".join(attributes)
        )

    def _extract_second_line_remitted_line(self, payload: bytes) -> str:
        """Return the exact direct RmtdAmt line from line two's Amount group."""
        text = payload.decode("utf-8")
        segment, _, _ = self.parent.parent._second_line_segment(text)
        lines = segment.splitlines(keepends=True)
        start, end = self.parent._direct_amount_bounds(lines)
        direct_indent = lines[start][
            : len(lines[start]) - len(lines[start].lstrip())
        ]
        child_candidates = [
            (index, line)
            for index, line in enumerate(lines[start + 1 : end], start + 1)
            if line.strip().startswith("<RmtdAmt ")
            and line.strip().endswith("</RmtdAmt>")
            and len(line) - len(line.lstrip()) > len(direct_indent)
        ]
        if len(child_candidates) != 1:
            raise AssertionError("second source line requires one direct RmtdAmt")
        index, line = child_candidates[0]
        if index != end - 1:
            raise AssertionError("RmtdAmt must remain the final populated Amount child")
        if not line.endswith("\n"):
            raise AssertionError("RmtdAmt fixture line must retain its source newline")
        return line

    def _remove_second_line_remitted_amount(self, payload: bytes) -> bytes:
        """Remove only the exact line-two RmtdAmt source line."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent.parent._second_line_segment(
            text
        )
        if segment.count(self.remitted_line) != 1:
            raise AssertionError("target RmtdAmt source line must occur exactly once")
        changed_segment = segment.replace(self.remitted_line, "", 1)
        changed_lines = changed_segment.splitlines(keepends=True)
        if any("RmtdAmt" in line for line in changed_lines):
            raise AssertionError("changed line-two Amount must omit RmtdAmt")
        self.parent._direct_amount_bounds(changed_lines)
        return (text[:segment_start] + changed_segment + text[segment_end:]).encode(
            "utf-8"
        )

    def _restore_second_line_remitted_amount(self, payload: bytes) -> bytes:
        """Restore the exact removed RmtdAmt line immediately before direct Amount closes."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent.parent._second_line_segment(
            text
        )
        lines = segment.splitlines(keepends=True)
        if any("RmtdAmt" in line for line in lines):
            raise AssertionError("RmtdAmt-absence fixture must not already contain it")
        _, end = self.parent._direct_amount_bounds(lines)
        restored_segment = "".join(
            lines[:end] + [self.remitted_line] + lines[end:]
        )
        return (text[:segment_start] + restored_segment + text[segment_end:]).encode(
            "utf-8"
        )


if __name__ == "__main__":
    unittest.main()
