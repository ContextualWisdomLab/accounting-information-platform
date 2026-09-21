"""REDs for source-faithful contraction of repeated line discount evidence."""

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
    lookup_bank_statement,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_optional_remitted_amount_absence_evidence_red
    as optional_remitted_contract,
)

_PARENT_TEST = (
    optional_remitted_contract.
    BankStatementStructuredLineOptionalRemittedAmountAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = optional_remitted_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = optional_remitted_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_DISCOUNT_KEY = "discount_applied_amounts"
_EXPECTED_DISCOUNTS = [
    {"type_code": "APDS", "amount": "100", "currency_code": "KRW"},
    {"type_code": "STDS", "amount": "50", "currency_code": "KRW"},
]


class BankStatementStructuredLineRepeatedDiscountContractionEvidenceRedTests(
    unittest.TestCase
):
    """Preserve removal of one repeated discount without collapsing its Amount group."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Remitted-Amount fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only the later second-line discount from #118's changed source."""
        self.parent = _PARENT_TEST(
            "test_optional_remitted_amount_absence_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # Compose on #118's changed source: DuePyblAmt and RmtdAmt are already
        # absent, while two repeated discounts keep the direct Amount group live.
        self.base_payload = self.parent.changed_payload
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.changed_projection)
        first_line, second_line = self.parent.parent.parent._line_details(
            self.base_projection
        )
        discounts = second_line.get(_DISCOUNT_KEY)
        if discounts != _EXPECTED_DISCOUNTS:
            raise AssertionError(
                "second source line must begin with APDS / 100 KRW then STDS / 50 KRW"
            )
        self.retained_discount = deepcopy(discounts[0])
        self.removed_discount = deepcopy(discounts[1])
        self.first_line_projection = deepcopy(first_line)
        self.second_line_non_discount_projection = deepcopy(second_line)
        self.second_line_non_discount_projection.pop(_DISCOUNT_KEY)
        if "due_payable_amount" in second_line or "remitted_amount" in second_line:
            raise AssertionError(
                "parent omission contracts must remove DuePyblAmt and RmtdAmt first"
            )

        base_blocks = self._second_line_discount_blocks(self.base_payload)
        if len(base_blocks) != 2:
            raise AssertionError("second source line must contain exactly two discounts")
        self.discount_gap = base_blocks[1][1]
        self.removed_discount_block = base_blocks[1][0]
        self.changed_payload = self._remove_later_second_line_discount(
            self.base_payload
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_later_discount()

    def test_later_discount_removal_is_material_to_each_evidence_hash(self) -> None:
        """Bind repeated-member contraction through raw and canonical evidence identity."""
        self.assertEqual(
            self._restore_later_second_line_discount(self.changed_payload),
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

    def test_rejected_discount_contraction_leaves_no_evidence_residue(self) -> None:
        """Reject one-member contraction without relational or raw-artifact residue."""
        restricted_owner = self.parent.parent.parent.parent._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-discount-contraction-base",
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
                    "line-discount-contraction-changed",
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

    def test_buyer_read_keeps_only_the_source_retained_discount(self) -> None:
        """Expose APDS only after the later STDS member disappears from source."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-discount-contraction-lookup",
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
        entries = document["bank_statement_entries"]
        self.assertEqual(len(entries), len(self.changed_statement.entries))
        entry = entries[0]
        detail = entry["entry_details"][0]
        changed_entry = self.changed_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]
        sibling_entry = entries[1]
        changed_sibling_entry = self.changed_statement.entries[1]

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
        self.assertEqual(
            sibling_entry["source_entry_hash"],
            changed_sibling_entry.source_entry_hash,
        )
        self.assertEqual(
            Decimal(str(sibling_entry["entry_amount"])),
            changed_sibling_entry.entry_amount,
        )
        self.assertEqual(
            sibling_entry["entry_currency_code"],
            changed_sibling_entry.entry_currency_code,
        )
        self.assertEqual(
            len(sibling_entry["entry_details"]),
            len(changed_sibling_entry.entry_details),
        )
        for persisted_detail, expected_detail in zip(
            sibling_entry["entry_details"],
            changed_sibling_entry.entry_details,
            strict=True,
        ):
            self.assertEqual(
                persisted_detail["source_detail_hash"],
                expected_detail.source_detail_hash,
            )
            self.assertEqual(
                Decimal(str(persisted_detail["detail_amount"])),
                expected_detail.detail_amount,
            )
            self.assertEqual(
                persisted_detail["detail_currency_code"],
                expected_detail.detail_currency_code,
            )

        first_line, second_line = self.parent.parent.parent._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.first_line_projection)
        second_line_without_discounts = deepcopy(second_line)
        discounts = second_line_without_discounts.pop(_DISCOUNT_KEY, None)
        self.assertEqual(discounts, [self.retained_discount])
        self.assertEqual(
            second_line_without_discounts,
            self.second_line_non_discount_projection,
        )
        self.assertNotIn("due_payable_amount", second_line)
        self.assertNotIn("remitted_amount", second_line)
        self.assertNotIn("description", second_line)

        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_later_discount(self) -> list[dict[str, object]]:
        """Remove only the later STDS member from line two's discount population."""
        projection = deepcopy(self.base_projection)
        first_line, second_line = self.parent.parent.parent._line_details(projection)
        discounts = second_line.get(_DISCOUNT_KEY)
        if discounts != [self.retained_discount, self.removed_discount]:
            raise AssertionError("projection discount population drifted before contraction")
        second_line[_DISCOUNT_KEY] = [deepcopy(discounts[0])]
        if first_line != self.first_line_projection:
            raise AssertionError("first source line must remain unchanged")
        stable_second_line = deepcopy(second_line)
        stable_second_line.pop(_DISCOUNT_KEY)
        if stable_second_line != self.second_line_non_discount_projection:
            raise AssertionError("non-discount line-two evidence must remain unchanged")
        return projection

    def _second_line_discount_blocks(self, payload: bytes) -> list[tuple[str, str]]:
        """Return direct discount blocks with exact inter-member source gaps."""
        text = payload.decode("utf-8")
        segment, _, _ = self.parent.parent.parent._second_line_segment(text)
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self.parent.parent._direct_amount_bounds(lines)
        amount_content_start = sum(len(line) for line in lines[: amount_start + 1])
        amount_content_end = sum(len(line) for line in lines[:amount_end])
        matches = list(
            re.finditer(
                r"<DscntApldAmt>.*?</DscntApldAmt>",
                segment,
                re.DOTALL,
            )
        )
        if any(
            match.start() < amount_content_start or match.end() > amount_content_end
            for match in matches
        ):
            raise AssertionError("discount blocks must remain inside the direct Amount group")
        blocks: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            gap = "" if index == 0 else segment[matches[index - 1].end() : match.start()]
            blocks.append((match.group(0), gap))
        return blocks

    def _remove_later_second_line_discount(self, payload: bytes) -> bytes:
        """Remove only the complete later STDS discount source block."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = (
            self.parent.parent.parent._second_line_segment(text)
        )
        blocks = self._second_line_discount_blocks(payload)
        if len(blocks) != 2:
            raise AssertionError("second source line must contain exactly two discounts")
        first_block, second_block = blocks[0][0], blocks[1][0]
        if "<Cd>APDS</Cd>" not in first_block or "<Cd>STDS</Cd>" not in second_block:
            raise AssertionError("source discount order must remain APDS then STDS")
        if "<Amt Ccy=\"KRW\">100.00</Amt>" not in first_block:
            raise AssertionError("first discount source amount must remain 100.00 KRW")
        if "<Amt Ccy=\"KRW\">50.00</Amt>" not in second_block:
            raise AssertionError("later discount source amount must remain 50.00 KRW")
        if segment.count(second_block) != 1:
            raise AssertionError("later discount block must occur exactly once")
        changed_segment = segment.replace(second_block, "", 1)
        changed_payload = (
            text[:segment_start] + changed_segment + text[segment_end:]
        ).encode("utf-8")
        remaining = self._second_line_discount_blocks(changed_payload)
        if len(remaining) != 1 or "<Cd>APDS</Cd>" not in remaining[0][0]:
            raise AssertionError("contraction must retain exactly the first APDS discount")
        return changed_payload

    def _restore_later_second_line_discount(self, payload: bytes) -> bytes:
        """Restore the exact removed STDS block at its original member boundary."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = (
            self.parent.parent.parent._second_line_segment(text)
        )
        remaining = self._second_line_discount_blocks(payload)
        if len(remaining) != 1 or "<Cd>APDS</Cd>" not in remaining[0][0]:
            raise AssertionError("contraction fixture must retain exactly one APDS discount")
        if "<Cd>STDS</Cd>" in segment:
            raise AssertionError("contraction fixture must not already contain STDS")
        first_block = remaining[0][0]
        first_end = segment.index(first_block) + len(first_block)
        if segment[first_end : first_end + len(self.discount_gap)] != self.discount_gap:
            raise AssertionError("inter-discount source gap must remain byte-exact")
        insert_at = first_end + len(self.discount_gap)
        restored_segment = (
            segment[:insert_at]
            + self.removed_discount_block
            + segment[insert_at:]
        )
        return (text[:segment_start] + restored_segment + text[segment_end:]).encode(
            "utf-8"
        )


if __name__ == "__main__":
    unittest.main()
