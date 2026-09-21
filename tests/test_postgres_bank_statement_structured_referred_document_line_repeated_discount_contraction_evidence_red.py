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
_APDS = {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
_STDS = {"type_code": "STDS", "amount": "50", "currency_code": "KRW"}
_LATER_DISCOUNT_SOURCE = (
    "                      <DscntApldAmt>\n"
    "                        <Tp><Cd>STDS</Cd></Tp>\n"
    "                        <Amt Ccy=\"KRW\">50.00</Amt>\n"
    "                      </DscntApldAmt>\n"
)


class BankStatementStructuredLineRepeatedDiscountContractionEvidenceRedTests(
    unittest.TestCase
):
    """Preserve removal of one repeated discount without collapsing its Amount group."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Remitted-Amount fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Seed two line-two discounts, then remove only the later STDS member."""
        self.parent = _PARENT_TEST(
            "test_optional_remitted_amount_absence_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # #118 intentionally owns a one-discount APDS source after DuePyblAmt and
        # RmtdAmt disappear. Seed the second STDS member here so this RED proves a
        # real 2 -> 1 repeated-member contraction instead of assuming ancestry.
        self.base_payload = self._seed_later_second_line_discount(
            self.parent.changed_payload
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = self._projection_with_later_discount(
            self.parent.changed_projection
        )
        first_line, second_line = self.parent.parent.parent._line_details(
            self.base_projection
        )
        discounts = second_line.get(_DISCOUNT_KEY)
        if discounts != [_APDS, _STDS]:
            raise AssertionError(
                "seeded second source line must contain APDS / 100 then STDS / 50 KRW"
            )
        if "due_payable_amount" in second_line or "remitted_amount" in second_line:
            raise AssertionError(
                "parent omission contracts must remove DuePyblAmt and RmtdAmt first"
            )
        self.first_line_projection = deepcopy(first_line)
        self.second_line_non_discount_projection = deepcopy(second_line)
        self.second_line_non_discount_projection.pop(_DISCOUNT_KEY)

        self.changed_payload = self._remove_later_second_line_discount(self.base_payload)
        if self.changed_payload != self.parent.changed_payload:
            raise AssertionError(
                "discount contraction must recover #118 exact changed-source bytes"
            )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_later_discount()
        if self.changed_projection != self.parent.changed_projection:
            raise AssertionError(
                "discount contraction must recover #118 exact changed projection"
            )

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
        """Expose APDS only after the seeded later STDS member disappears from source."""
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
        self.assertEqual(discounts, [_APDS])
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

    def _projection_with_later_discount(
        self,
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Seed one later STDS member after the exact inherited APDS discount."""
        changed = deepcopy(projection)
        _, second_line = self.parent.parent.parent._line_details(changed)
        discounts = second_line.get(_DISCOUNT_KEY)
        if discounts != [_APDS]:
            raise AssertionError("#118 source must begin with exactly APDS / 100 KRW")
        discounts.append(deepcopy(_STDS))
        return changed

    def _projection_without_later_discount(self) -> list[dict[str, object]]:
        """Contract the seeded APDS/STDS population back to inherited APDS only."""
        projection = deepcopy(self.base_projection)
        first_line, second_line = self.parent.parent.parent._line_details(projection)
        discounts = second_line.get(_DISCOUNT_KEY)
        if discounts != [_APDS, _STDS]:
            raise AssertionError("projection must begin with seeded APDS then STDS")
        second_line[_DISCOUNT_KEY] = [deepcopy(_APDS)]
        if first_line != self.first_line_projection:
            raise AssertionError("first source line must remain unchanged")
        stable_second_line = deepcopy(second_line)
        stable_second_line.pop(_DISCOUNT_KEY)
        if stable_second_line != self.second_line_non_discount_projection:
            raise AssertionError("non-discount line-two evidence must remain unchanged")
        return projection

    def _second_line_discount_blocks(self, payload: bytes) -> list[str]:
        """Return complete direct discount blocks from the line-two Amount group."""
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
        return [match.group(0) for match in matches]

    def _seed_later_second_line_discount(self, payload: bytes) -> bytes:
        """Append the exact STDS fixture block after the inherited APDS member."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = (
            self.parent.parent.parent._second_line_segment(text)
        )
        blocks = self._second_line_discount_blocks(payload)
        if len(blocks) != 1 or "<Cd>APDS</Cd>" not in blocks[0]:
            raise AssertionError("#118 source must contain exactly one APDS discount")
        if "<Amt Ccy=\"KRW\">100.00</Amt>" not in blocks[0]:
            raise AssertionError("inherited APDS amount must remain 100.00 KRW")
        if _LATER_DISCOUNT_SOURCE in segment or "<Cd>STDS</Cd>" in segment:
            raise AssertionError("#118 source must not already contain STDS")
        first_end = segment.index(blocks[0]) + len(blocks[0])
        if segment[first_end : first_end + 1] != "\n":
            raise AssertionError("inherited APDS block must retain its source newline")
        insert_at = first_end + 1
        seeded_segment = (
            segment[:insert_at]
            + _LATER_DISCOUNT_SOURCE
            + segment[insert_at:]
        )
        seeded = (text[:segment_start] + seeded_segment + text[segment_end:]).encode(
            "utf-8"
        )
        blocks = self._second_line_discount_blocks(seeded)
        if len(blocks) != 2 or "<Cd>STDS</Cd>" not in blocks[1]:
            raise AssertionError("seeded source must contain APDS then STDS")
        return seeded

    def _remove_later_second_line_discount(self, payload: bytes) -> bytes:
        """Remove only the exact seeded STDS block, including its source newline."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = (
            self.parent.parent.parent._second_line_segment(text)
        )
        blocks = self._second_line_discount_blocks(payload)
        if len(blocks) != 2:
            raise AssertionError("seeded line must contain exactly two discounts")
        if "<Cd>APDS</Cd>" not in blocks[0] or "<Cd>STDS</Cd>" not in blocks[1]:
            raise AssertionError("source discount order must remain APDS then STDS")
        if segment.count(_LATER_DISCOUNT_SOURCE) != 1:
            raise AssertionError("exact seeded STDS source block must occur once")
        changed_segment = segment.replace(_LATER_DISCOUNT_SOURCE, "", 1)
        changed = (text[:segment_start] + changed_segment + text[segment_end:]).encode(
            "utf-8"
        )
        remaining = self._second_line_discount_blocks(changed)
        if len(remaining) != 1 or "<Cd>APDS</Cd>" not in remaining[0]:
            raise AssertionError("contraction must retain exactly the inherited APDS member")
        return changed

    def _restore_later_second_line_discount(self, payload: bytes) -> bytes:
        """Restore the exact seeded STDS member at its original source boundary."""
        if "<Cd>STDS</Cd>" in payload.decode("utf-8"):
            raise AssertionError("contracted source must not already contain STDS")
        return self._seed_later_second_line_discount(payload)


if __name__ == "__main__":
    unittest.main()
