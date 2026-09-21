"""REDs for source-faithful absence of line adjustment additional information."""

from __future__ import annotations

import hashlib
import unittest
from copy import deepcopy
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    parse_bank_statement_payload,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_adjustment_reason_absence_evidence_red
    as reason_absence_contract,
)

_CORRECTION_ERROR = reason_absence_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_ADJUSTMENT_KEY = "adjustments"
_STAT = {"type_code": "STAT", "amount": "950", "currency_code": "KRW"}
_APDS = {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
_ADJT_AMOUNT_AND_TEXT = {
    "amount": "200",
    "currency_code": "KRW",
    "additional_information": "Contract true-up",
}
_ADJT_AMOUNT_ONLY = {
    "amount": "200",
    "currency_code": "KRW",
}


class BankStatementStructuredLineAdjustmentAdditionalInfoAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve optional AddtlInf absence on a retained line adjustment."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL reason-absence fixture."""
        contract_type = (
            reason_absence_contract.
            BankStatementStructuredLineAdjustmentReasonAbsenceEvidenceRedTests
        )
        contract_type.setUpClass()

    def setUp(self) -> None:
        """Start from #132 changed source and remove only additional information."""
        contract_type = (
            reason_absence_contract.
            BankStatementStructuredLineAdjustmentReasonAbsenceEvidenceRedTests
        )
        self.parent = contract_type(
            "test_reason_absence_reads_back_without_stale_or_default_value"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.direction_contract = self.parent.parent
        self.line_contract = self.parent.line_contract
        self.line_source_contract = self.parent.line_source_contract

        self.base_payload = self.parent.changed_payload
        self.base_statement = self.parent.changed_statement
        self.base_projection = deepcopy(self.parent.changed_projection)
        first_line, second_line = self.line_source_contract._line_details(
            self.base_projection
        )
        if second_line.get(_ADJUSTMENT_KEY) != [_ADJT_AMOUNT_AND_TEXT]:
            raise AssertionError(
                "#132 changed source must retain amount plus additional information"
            )
        if second_line.get("tax_amounts") != [_STAT]:
            raise AssertionError("Stockitem2 must retain STAT / 950 KRW")
        if second_line.get("discount_applied_amounts") != [_APDS]:
            raise AssertionError(
                "Stockitem2 must retain APDS / 100 KRW so direct Amount survives"
            )
        self.first_line_projection = deepcopy(first_line)
        self.second_line_without_adjustments = deepcopy(second_line)
        self.second_line_without_adjustments.pop(_ADJUSTMENT_KEY)

        (
            self.changed_payload,
            self.removed_information_line,
            self.removed_information_offset,
        ) = self._remove_adjustment_additional_information(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = deepcopy(self.base_projection)
        changed_lines = self.line_source_contract._line_details(
            self.changed_projection
        )
        adjustments = changed_lines[1].get(_ADJUSTMENT_KEY)
        if adjustments != [_ADJT_AMOUNT_AND_TEXT]:
            raise AssertionError("changed projection must begin from #132 exact ADJT")
        removed = adjustments[0].pop("additional_information", None)
        if removed != "Contract true-up":
            raise AssertionError(
                "changed projection must remove exact adjustment additional info"
            )
        if adjustments != [_ADJT_AMOUNT_ONLY]:
            raise AssertionError(
                "only adjustment additional information may become additionally absent"
            )

        restored = self._restore_adjustment_additional_information(
            self.changed_payload,
            self.removed_information_line,
            self.removed_information_offset,
        )
        if restored != self.base_payload:
            raise AssertionError(
                "exact removed additional-info bytes must restore #132 source"
            )

    def test_adjustment_additional_info_absence_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind AddtlInf omission to raw and canonical evidence identity."""
        self.assertEqual(
            self._restore_adjustment_additional_information(
                self.changed_payload,
                self.removed_information_line,
                self.removed_information_offset,
            ),
            self.base_payload,
        )
        normalization_contract = (
            reason_absence_contract.direction_absence_contract.
            adjustment_absence_contract.adjustment_contract.
            _NORMALIZATION_PARENT_TEST
        )
        normalization_contract._assert_non_structured_normalization_unchanged(self)

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

    def test_additional_info_present_baseline_reads_back_before_absence(self) -> None:
        """Persist Contract true-up before proving independent AddtlInf absence."""
        statement, entries = self.direction_contract._persist_and_read(
            self.base_payload,
            self.base_statement,
            "line-adjustment-additional-info-present-baseline",
        )
        self.direction_contract._assert_statement_hashes(
            statement,
            self.base_statement,
        )
        self._assert_primary_entry(
            entries[0],
            self.base_statement.entries[0],
            self.base_projection,
            _ADJT_AMOUNT_AND_TEXT,
        )
        self.direction_contract._assert_complete_sibling(
            entries[1],
            self.base_statement.entries[1],
        )

    def test_additional_info_absence_reads_back_without_stale_or_default_text(
        self,
    ) -> None:
        """Persist AddtlInf omission while retaining the mandatory amount."""
        statement, entries = self.direction_contract._persist_and_read(
            self.changed_payload,
            self.changed_statement,
            "line-adjustment-additional-info-absence-lookup",
        )
        self.direction_contract._assert_statement_hashes(
            statement,
            self.changed_statement,
        )
        self._assert_primary_entry(
            entries[0],
            self.changed_statement.entries[0],
            self.changed_projection,
            _ADJT_AMOUNT_ONLY,
        )
        self.direction_contract._assert_complete_sibling(
            entries[1],
            self.changed_statement.entries[1],
        )

    def test_rejected_additional_info_absence_leaves_no_evidence_residue(
        self,
    ) -> None:
        """Reject text-only contraction without relational or raw residue."""
        repeated_adjustment_owner = self.direction_contract.contract.parent
        restricted_owner = repeated_adjustment_owner._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-adjustment-additional-info-absence-base",
            ),
            runtime_url,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        before_rows = restricted_owner._tenant_statement_rows(runtime_url)
        expected_artifacts = {
            self.base_statement.source_artifact_hash: self.base_payload,
        }
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertTrue(before_rows["bank_statement_artifact"])
        self.assertTrue(before_rows["bank_statement_entry"])
        self.assertTrue(before_rows["bank_statement_entry_detail"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "line-adjustment-additional-info-absence-changed",
                ),
                runtime_url,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(
            restricted_owner._tenant_statement_rows(runtime_url),
            before_rows,
        )
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def _adjustment_blocks(self, payload: bytes) -> list[str]:
        """Return direct adjustment blocks through the canonical #124 helper."""
        return self.direction_contract.contract.parent._adjustment_blocks(payload)

    def _remove_adjustment_additional_information(
        self,
        payload: bytes,
    ) -> tuple[bytes, bytes, int]:
        """Remove exact AddtlInf line from #132's amount-only-plus-text tuple."""
        blocks = self._adjustment_blocks(payload)
        if len(blocks) != 1:
            raise AssertionError("#132 source must expose exactly one adjustment block")
        block = blocks[0]
        if "<CdtDbtInd>" in block or "<Rsn>" in block:
            raise AssertionError("#132 source must already omit direction and reason")
        for marker in (
            '<Amt Ccy="KRW">200.00</Amt>',
            "<AddtlInf>Contract true-up</AddtlInf>",
        ):
            if marker not in block:
                raise AssertionError("baseline adjustment tuple is not #132-canonical")

        lines = block.splitlines(keepends=True)
        matches = [
            line
            for line in lines
            if line.strip() == "<AddtlInf>Contract true-up</AddtlInf>"
        ]
        if len(matches) != 1:
            raise AssertionError("retained adjustment must expose one AddtlInf line")
        removed_text = matches[0]
        block_bytes = block.encode("utf-8")
        removed_bytes = removed_text.encode("utf-8")
        if payload.count(block_bytes) != 1:
            raise AssertionError("retained adjustment block must occur exactly once")
        block_offset = payload.index(block_bytes)
        local_offset = block_bytes.index(removed_bytes)
        offset = block_offset + local_offset
        if payload[offset : offset + len(removed_bytes)] != removed_bytes:
            raise AssertionError("AddtlInf byte offset must identify exact source bytes")
        changed = payload[:offset] + payload[offset + len(removed_bytes) :]

        changed_blocks = self._adjustment_blocks(changed)
        if len(changed_blocks) != 1:
            raise AssertionError("AddtlInf omission must retain the adjustment block")
        changed_block = changed_blocks[0]
        if (
            "<CdtDbtInd>" in changed_block
            or "<Rsn>" in changed_block
            or "<AddtlInf>" in changed_block
        ):
            raise AssertionError(
                "changed adjustment must omit direction, reason and additional info"
            )
        if '<Amt Ccy="KRW">200.00</Amt>' not in changed_block:
            raise AssertionError("mandatory adjustment amount must remain exact")
        changed_segment, _, _ = self.line_source_contract._line_segment(
            changed,
            "Stockitem2",
        )
        if "<TaxAmt>" not in changed_segment or "<DscntApldAmt>" not in changed_segment:
            raise AssertionError(
                "STAT and APDS must keep Stockitem2 direct Amount populated"
            )
        return changed, removed_bytes, offset

    def _restore_adjustment_additional_information(
        self,
        payload: bytes,
        removed_line: bytes,
        offset: int,
    ) -> bytes:
        """Reinsert exact removed AddtlInf bytes at their original byte offset."""
        if not 0 <= offset <= len(payload):
            raise AssertionError("removed AddtlInf offset must stay within byte bounds")
        restored = payload[:offset] + removed_line + payload[offset:]
        blocks = self._adjustment_blocks(restored)
        if len(blocks) != 1:
            raise AssertionError("restoration must recover one adjustment block")
        block = blocks[0]
        if "<CdtDbtInd>" in block or "<Rsn>" in block:
            raise AssertionError("restoration must not manufacture direction or reason")
        for marker in (
            '<Amt Ccy="KRW">200.00</Amt>',
            "<AddtlInf>Contract true-up</AddtlInf>",
        ):
            if marker not in block:
                raise AssertionError("restoration must recover #132 baseline tuple")
        return restored

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        expected_adjustment: dict[str, str],
    ) -> None:
        """Assert AddtlInf presence/absence without changing transaction truth."""
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

        first_line, second_line = self.line_source_contract._line_details(
            persisted_detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.first_line_projection)
        self.assertEqual(second_line.get(_ADJUSTMENT_KEY), [expected_adjustment])
        stable_second = deepcopy(second_line)
        stable_second.pop(_ADJUSTMENT_KEY)
        self.assertEqual(stable_second, self.second_line_without_adjustments)
        self.assertEqual(second_line.get("tax_amounts"), [_STAT])
        self.assertEqual(second_line.get("discount_applied_amounts"), [_APDS])
        for absent_key in (
            "credit_note_amount",
            "due_payable_amount",
            "remitted_amount",
            "description",
        ):
            self.assertNotIn(absent_key, second_line)

        persisted_adjustment = second_line[_ADJUSTMENT_KEY][0]
        self.assertNotIn("credit_debit_code", persisted_adjustment)
        self.assertNotIn("reason_code", persisted_adjustment)
        if "additional_information" in expected_adjustment:
            self.assertEqual(
                persisted_adjustment["additional_information"],
                "Contract true-up",
            )
        else:
            self.assertNotIn("additional_information", persisted_adjustment)
            self.assertEqual(persisted_adjustment, _ADJT_AMOUNT_ONLY)

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
