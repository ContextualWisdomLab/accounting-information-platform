"""REDs for optional referred-document line Due Payable Amount absence evidence."""

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
    test_postgres_bank_statement_structured_referred_document_line_optional_amount_absence_evidence_red
    as optional_amount_contract,
)

_PARENT_TEST = (
    optional_amount_contract.BankStatementStructuredLineOptionalAmountAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = optional_amount_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = optional_amount_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_TARGET_KEY = "due_payable_amount"
_OTHER_AMOUNT_KEYS = tuple(
    key for key in optional_amount_contract._AMOUNT_EVIDENCE_KEYS if key != _TARGET_KEY
)
_REQUIRED_RETAINED_KEYS = (
    "discount_applied_amounts",
    "remitted_amount",
)


class BankStatementStructuredLineOptionalDuePayableAmountAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve Due Payable Amount absence on the exact source LineDtls member."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Amount fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only line-two DuePyblAmt while preserving the rest of Amount."""
        self.parent = _PARENT_TEST(
            "test_optional_amount_absence_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # Use #116's base source, not its changed source: the direct Amount group
        # remains present, while inherited Description/Type omissions stay live.
        self.base_payload = self.parent.base_payload
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.base_projection)
        first_line, second_line = self.parent._line_details(self.base_projection)
        self.second_line_number = str(second_line["line_number"])
        self.first_line_amount_evidence = {
            key: deepcopy(first_line[key])
            for key in optional_amount_contract._AMOUNT_EVIDENCE_KEYS
            if key in first_line
        }
        self.second_line_other_amount_evidence = {
            key: deepcopy(second_line[key])
            for key in _OTHER_AMOUNT_KEYS
            if key in second_line
        }
        missing_required = [
            key for key in _REQUIRED_RETAINED_KEYS
            if key not in self.second_line_other_amount_evidence
        ]
        if missing_required:
            raise AssertionError(
                "second source line must retain populated non-target Amount evidence: "
                + ", ".join(missing_required)
            )
        self.second_line_absent_optional_amount_keys = tuple(
            key for key in _OTHER_AMOUNT_KEYS if key not in second_line
        )

        self.due_payable_line = self._extract_second_line_due_payable_line(
            self.base_payload
        )
        self.changed_payload = self._remove_second_line_due_payable_amount(
            self.base_payload
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_second_line_due_payable()

    def test_optional_due_payable_absence_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind one source-line Due Payable Amount omission through evidence identity."""
        self.assertEqual(
            self._restore_second_line_due_payable_amount(self.changed_payload),
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

    def test_rejected_due_payable_absence_change_leaves_no_evidence_residue(
        self,
    ) -> None:
        """Reject DuePyblAmt omission without changing relational or raw evidence."""
        restricted_owner = self.parent.parent._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-optional-due-payable-base",
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
                    "line-optional-due-payable-absent",
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

    def test_buyer_read_keeps_due_payable_absent_on_exact_source_line(self) -> None:
        """Expose no line-two Due Payable Amount without erasing sibling monetary evidence."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-optional-due-payable-lookup",
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

        first_line, second_line = self.parent._line_details(
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
        self.assertNotIn("description", second_line)
        self.assertEqual(second_line["line_number"], self.second_line_number)

        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_second_line_due_payable(
        self,
    ) -> list[dict[str, object]]:
        """Remove only Due Payable Amount from line two's canonical projection."""
        projection = deepcopy(self.base_projection)
        first_line, second_line = self.parent._line_details(projection)
        if _TARGET_KEY not in second_line:
            raise AssertionError("second source line must begin with Due Payable Amount")
        second_line.pop(_TARGET_KEY)
        for key, expected in self.second_line_other_amount_evidence.items():
            if second_line.get(key) != expected:
                raise AssertionError(
                    "populated non-target line-two Amount evidence must remain unchanged"
                )
        for key in self.second_line_absent_optional_amount_keys:
            if key in second_line:
                raise AssertionError(
                    "Due Payable omission must not manufacture absent optional Amount evidence"
                )
        for key, expected in self.first_line_amount_evidence.items():
            if first_line.get(key) != expected:
                raise AssertionError("first source line Amount evidence must remain unchanged")
        return projection

    def _extract_second_line_due_payable_line(self, payload: bytes) -> str:
        """Return the exact direct DuePyblAmt line from line two's Amount group."""
        text = payload.decode("utf-8")
        segment, _, _ = self.parent._second_line_segment(text)
        lines = segment.splitlines(keepends=True)
        amount_open = [
            index for index, line in enumerate(lines) if line.strip() == "<Amt>"
        ]
        amount_close = [
            index for index, line in enumerate(lines) if line.strip() == "</Amt>"
        ]
        if len(amount_open) != 1 or len(amount_close) != 1:
            raise AssertionError("second source line requires one direct Amount group")
        start = amount_open[0]
        end = amount_close[0]
        if not start < end:
            raise AssertionError("direct Amount group boundaries are invalid")

        candidates = [
            (index, line)
            for index, line in enumerate(lines[start + 1 : end], start + 1)
            if line.strip().startswith("<DuePyblAmt ")
            and line.strip().endswith("</DuePyblAmt>")
        ]
        if len(candidates) != 1:
            raise AssertionError("second source line requires one direct DuePyblAmt")
        index, line = candidates[0]
        if index != start + 1:
            raise AssertionError("DuePyblAmt must remain the first populated Amount child")
        if not line.endswith("\n"):
            raise AssertionError("DuePyblAmt fixture line must retain its source newline")
        return line

    def _remove_second_line_due_payable_amount(self, payload: bytes) -> bytes:
        """Remove only the exact line-two DuePyblAmt source line."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent._second_line_segment(text)
        if segment.count(self.due_payable_line) != 1:
            raise AssertionError("target DuePyblAmt source line must occur exactly once")
        changed_segment = segment.replace(self.due_payable_line, "", 1)
        changed_lines = changed_segment.splitlines(keepends=True)
        if any("DuePyblAmt" in line for line in changed_lines):
            raise AssertionError("changed line-two Amount must omit DuePyblAmt")
        if sum(line.strip() == "<Amt>" for line in changed_lines) != 1:
            raise AssertionError("direct Amount group must remain after DuePyblAmt omission")
        if sum(line.strip() == "</Amt>" for line in changed_lines) != 1:
            raise AssertionError("direct Amount group must remain closed")
        return (text[:segment_start] + changed_segment + text[segment_end:]).encode(
            "utf-8"
        )

    def _restore_second_line_due_payable_amount(self, payload: bytes) -> bytes:
        """Restore the exact removed DuePyblAmt line immediately after direct Amount opens."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent._second_line_segment(text)
        lines = segment.splitlines(keepends=True)
        if any("DuePyblAmt" in line for line in lines):
            raise AssertionError("DuePyblAmt-absence fixture must not already contain it")
        open_indexes = [
            index for index, line in enumerate(lines) if line.strip() == "<Amt>"
        ]
        if len(open_indexes) != 1:
            raise AssertionError("second source line requires one direct Amount opener")
        insert_at = open_indexes[0] + 1
        restored_segment = "".join(
            lines[:insert_at] + [self.due_payable_line] + lines[insert_at:]
        )
        return (text[:segment_start] + restored_segment + text[segment_end:]).encode(
            "utf-8"
        )


if __name__ == "__main__":
    unittest.main()
