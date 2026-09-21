"""REDs for source-faithful absence of line adjustment credit/debit evidence."""

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
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_adjustment_absence_evidence_red
    as adjustment_absence_contract,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_discount_absence_sibling_readback_red
    as sibling_readback_contract,
)

_CORRECTION_ERROR = adjustment_absence_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_ADJUSTMENT_KEY = "adjustments"
_STAT = {"type_code": "STAT", "amount": "950", "currency_code": "KRW"}
_APDS = {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
_ADJT_WITH_DIRECTION = {
    "amount": "200",
    "currency_code": "KRW",
    "credit_debit_code": "CRDT",
    "reason_code": "ADJT",
    "additional_information": "Contract true-up",
}
_ADJT_WITHOUT_DIRECTION = {
    "amount": "200",
    "currency_code": "KRW",
    "reason_code": "ADJT",
    "additional_information": "Contract true-up",
}


class BankStatementStructuredLineAdjustmentDirectionAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve optional CdtDbtInd absence on a retained line adjustment."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL adjustment-absence fixture."""
        contract_type = (
            adjustment_absence_contract.
            BankStatementStructuredLineAdjustmentAbsenceEvidenceRedTests
        )
        contract_type.setUpClass()

    def setUp(self) -> None:
        """Start from #130's one-ADJT base and remove only its direction line."""
        contract_type = (
            adjustment_absence_contract.
            BankStatementStructuredLineAdjustmentAbsenceEvidenceRedTests
        )
        self.contract = contract_type(
            "test_adjustment_present_baseline_reads_back_before_zero_population"
        )
        self.contract.setUp()
        self.addCleanup(self.contract.doCleanups)
        self.line_contract = self.contract.line_contract
        self.line_source_contract = self.contract.line_source_contract

        self.base_payload = self.contract.base_payload
        self.base_statement = self.contract.base_statement
        self.base_projection = deepcopy(self.contract.base_projection)
        first_line, second_line = self.line_source_contract._line_details(
            self.base_projection
        )
        if second_line.get(_ADJUSTMENT_KEY) != [_ADJT_WITH_DIRECTION]:
            raise AssertionError(
                "#130 base must retain exactly ADJT / 200 KRW / CRDT"
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
            self.removed_direction_line,
            self.removed_direction_offset,
        ) = self._remove_adjustment_direction(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = deepcopy(self.base_projection)
        changed_lines = self.line_source_contract._line_details(
            self.changed_projection
        )
        adjustments = changed_lines[1].get(_ADJUSTMENT_KEY)
        if adjustments != [_ADJT_WITH_DIRECTION]:
            raise AssertionError("changed projection must begin from exact ADJT tuple")
        removed = adjustments[0].pop("credit_debit_code", None)
        if removed != "CRDT":
            raise AssertionError("changed projection must remove exact CRDT direction")
        if adjustments != [_ADJT_WITHOUT_DIRECTION]:
            raise AssertionError("only adjustment direction may become absent")

        restored = self._restore_adjustment_direction(
            self.changed_payload,
            self.removed_direction_line,
            self.removed_direction_offset,
        )
        if restored != self.base_payload:
            raise AssertionError(
                "exact removed direction bytes must restore the one-ADJT source"
            )

    def test_adjustment_direction_absence_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind CdtDbtInd omission to raw and canonical evidence identity."""
        self.assertEqual(
            self._restore_adjustment_direction(
                self.changed_payload,
                self.removed_direction_line,
                self.removed_direction_offset,
            ),
            self.base_payload,
        )
        normalization_contract = (
            adjustment_absence_contract.adjustment_contract._NORMALIZATION_PARENT_TEST
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

    def test_direction_present_baseline_reads_back_before_absence(self) -> None:
        """Persist CRDT first so unconditional direction loss cannot false-GREEN."""
        statement, entries = self._persist_and_read(
            self.base_payload,
            self.base_statement,
            "line-adjustment-direction-present-baseline",
        )
        self._assert_statement_hashes(statement, self.base_statement)
        self._assert_primary_entry(
            entries[0],
            self.base_statement.entries[0],
            self.base_projection,
            _ADJT_WITH_DIRECTION,
        )
        self._assert_complete_sibling(
            entries[1],
            self.base_statement.entries[1],
        )

    def test_direction_absence_reads_back_without_stale_or_default_value(
        self,
    ) -> None:
        """Persist direction omission while retaining the rest of the ADJT tuple."""
        statement, entries = self._persist_and_read(
            self.changed_payload,
            self.changed_statement,
            "line-adjustment-direction-absence-lookup",
        )
        self._assert_statement_hashes(statement, self.changed_statement)
        self._assert_primary_entry(
            entries[0],
            self.changed_statement.entries[0],
            self.changed_projection,
            _ADJT_WITHOUT_DIRECTION,
        )
        self._assert_complete_sibling(
            entries[1],
            self.changed_statement.entries[1],
        )

    def test_rejected_direction_absence_leaves_no_evidence_residue(self) -> None:
        """Reject direction-only contraction without relational or raw residue."""
        restricted_owner = self.contract.parent._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-adjustment-direction-absence-base",
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
                    "line-adjustment-direction-absence-changed",
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

    def _remove_adjustment_direction(
        self,
        payload: bytes,
    ) -> tuple[bytes, bytes, int]:
        """Remove exact CRDT source line from the sole retained ADJT block."""
        blocks = self.contract.parent._adjustment_blocks(payload)
        if len(blocks) != 1:
            raise AssertionError("#130 base must expose exactly one adjustment block")
        block = blocks[0]
        if self.contract.parent._adjustment_identity(block) != "ADJT":
            raise AssertionError("retained adjustment must be exact ADJT tuple")
        lines = block.splitlines(keepends=True)
        matches = [
            line for line in lines if line.strip() == "<CdtDbtInd>CRDT</CdtDbtInd>"
        ]
        if len(matches) != 1:
            raise AssertionError("ADJT must expose exactly one direct CRDT line")
        removed_text = matches[0]
        block_bytes = block.encode("utf-8")
        removed_bytes = removed_text.encode("utf-8")
        if payload.count(block_bytes) != 1:
            raise AssertionError("retained ADJT block must occur exactly once")
        block_offset = payload.index(block_bytes)
        local_offset = block_bytes.index(removed_bytes)
        offset = block_offset + local_offset
        if payload[offset : offset + len(removed_bytes)] != removed_bytes:
            raise AssertionError("direction byte offset must identify exact source bytes")
        changed = payload[:offset] + payload[offset + len(removed_bytes) :]

        changed_blocks = self.contract.parent._adjustment_blocks(changed)
        if len(changed_blocks) != 1:
            raise AssertionError("direction omission must retain the ADJT block")
        changed_block = changed_blocks[0]
        for marker in (
            '<Amt Ccy="KRW">200.00</Amt>',
            "<Rsn>ADJT</Rsn>",
            "<AddtlInf>Contract true-up</AddtlInf>",
        ):
            if marker not in changed_block:
                raise AssertionError(
                    "direction omission must preserve adjustment amount/reason/text"
                )
        if "<CdtDbtInd>" in changed_block:
            raise AssertionError("changed ADJT block must omit credit/debit direction")
        changed_segment, _, _ = self.line_source_contract._line_segment(
            changed,
            "Stockitem2",
        )
        if "<TaxAmt>" not in changed_segment or "<DscntApldAmt>" not in changed_segment:
            raise AssertionError(
                "STAT and APDS must keep Stockitem2 direct Amount populated"
            )
        return changed, removed_bytes, offset

    def _restore_adjustment_direction(
        self,
        payload: bytes,
        removed_line: bytes,
        offset: int,
    ) -> bytes:
        """Reinsert exact removed direction bytes at their original byte offset."""
        if not 0 <= offset <= len(payload):
            raise AssertionError("removed direction offset must stay within byte bounds")
        restored = payload[:offset] + removed_line + payload[offset:]
        blocks = self.contract.parent._adjustment_blocks(restored)
        if len(blocks) != 1:
            raise AssertionError("restoration must recover one ADJT block")
        if self.contract.parent._adjustment_identity(blocks[0]) != "ADJT":
            raise AssertionError("restoration must recover exact ADJT / CRDT tuple")
        return restored

    def _persist_and_read(
        self,
        payload: bytes,
        expected_statement: object,
        idempotency_key: str,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Persist one source and return complete statement and entry readback."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(payload, idempotency_key),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        self.assertEqual(
            self.line_contract.store._artifacts,
            {expected_statement.source_artifact_hash: payload},
        )
        return self.contract.parent._lookup(
            str(accepted["bank_statement_record_id"]),
            expected_statement,
        )

    def _assert_statement_hashes(
        self,
        persisted: dict[str, object],
        expected: object,
    ) -> None:
        """Bind persisted raw and normalized hashes to parsed source evidence."""
        self.contract.parent._assert_statement_hashes(persisted, expected)

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        expected_adjustment: dict[str, str],
    ) -> None:
        """Assert exact adjustment direction presence/absence and transaction truth."""
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
        if "credit_debit_code" in expected_adjustment:
            self.assertEqual(persisted_adjustment["credit_debit_code"], "CRDT")
        else:
            self.assertNotIn("credit_debit_code", persisted_adjustment)
            self.assertEqual(persisted_adjustment, _ADJT_WITHOUT_DIRECTION)

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

    def _assert_complete_sibling(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
    ) -> None:
        """Reuse the complete public sibling-entry/detail isolation oracle."""
        helper_type = (
            sibling_readback_contract.
            BankStatementStructuredLineDiscountAbsenceSiblingReadbackRedTests
        )
        helper = helper_type(
            "test_discount_present_baseline_reads_complete_sibling_projection"
        )
        helper._assert_complete_sibling(persisted_entry, expected_entry)


if __name__ == "__main__":
    unittest.main()
