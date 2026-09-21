"""REDs for source-faithful contraction of repeated line adjustment evidence."""

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
    test_postgres_bank_statement_structured_referred_document_line_repeated_tax_contraction_evidence_red
    as repeated_tax_contract,
)

_PARENT_TEST = (
    repeated_tax_contract.
    BankStatementStructuredLineRepeatedTaxContractionEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = repeated_tax_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = repeated_tax_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_ADJUSTMENT_KEY = "adjustments"
_ADJT = {
    "amount": "200",
    "currency_code": "KRW",
    "credit_debit_code": "CRDT",
    "reason_code": "ADJT",
    "additional_information": "Contract true-up",
}
_FEES = {
    "amount": "50",
    "currency_code": "KRW",
    "credit_debit_code": "DBIT",
    "reason_code": "FEES",
    "additional_information": "Processing fee",
}


class BankStatementStructuredLineRepeatedAdjustmentContractionEvidenceRedTests(
    unittest.TestCase
):
    """Preserve a 2-to-1 repeated adjustment contraction on the exact source line."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-tax contraction fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Seed two line-two adjustments, then remove only the later FEES member."""
        self.parent = _PARENT_TEST(
            "test_later_tax_removal_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract
        self.line_source_contract = self.parent.parent

        # Compose on #123's contracted-tax source so STAT / 950 KRW remains
        # independently populated before the adjustment population is changed.
        self.inherited_payload = self.parent.changed_payload
        self.inherited_projection = deepcopy(self.parent.changed_projection)
        first_inherited, second_inherited = self.line_source_contract._line_details(
            self.inherited_projection
        )
        if second_inherited.get("tax_amounts") != [
            deepcopy(repeated_tax_contract._STAT)
        ]:
            raise AssertionError(
                "#123 changed source must retain exactly STAT / 950 KRW"
            )
        if second_inherited.get("discount_applied_amounts") != [
            {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
        ]:
            raise AssertionError(
                "#123 changed source must retain exactly APDS / 100 KRW"
            )
        if _ADJUSTMENT_KEY in first_inherited or _ADJUSTMENT_KEY in second_inherited:
            raise AssertionError(
                "#123 inherited projection must begin with no line adjustments"
            )

        self.base_payload = self._seed_second_line_adjustment(
            self.inherited_payload,
            _ADJT,
        )
        self.base_payload = self._seed_second_line_adjustment(
            self.base_payload,
            _FEES,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = self._projection_with_adjustments(
            self.inherited_projection,
            [_ADJT, _FEES],
        )

        self.changed_payload = self._remove_later_second_line_adjustment(
            self.base_payload
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_with_adjustments(
            self.inherited_projection,
            [_ADJT],
        )
        if (
            self._restore_later_second_line_adjustment(self.changed_payload)
            != self.base_payload
        ):
            raise AssertionError(
                "later adjustment restoration must recover exact two-member source bytes"
            )

        self.first_line_projection, second_line = (
            self.line_source_contract._line_details(self.changed_projection)
        )
        self.first_line_projection = deepcopy(self.first_line_projection)
        self.second_line_without_adjustments = deepcopy(second_line)
        self.second_line_without_adjustments.pop(_ADJUSTMENT_KEY)

    def test_later_adjustment_removal_is_material_to_each_evidence_hash(self) -> None:
        """Bind repeated adjustment contraction through raw and canonical identity."""
        self.assertEqual(
            self._restore_later_second_line_adjustment(self.changed_payload),
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

    def test_two_adjustment_baseline_persists_and_reads_back_in_source_order(self) -> None:
        """Read back ADJT then FEES before exercising the 2-to-1 contraction."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-adjustment-contraction-two-member-baseline",
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
        statement, entries = self._lookup(str(accepted["bank_statement_record_id"]))
        self._assert_persisted_statement_identity(statement, self.base_statement)

        entry = entries[0]
        detail = entry["entry_details"][0]
        expected_entry = self.base_statement.entries[0]
        expected_detail = expected_entry.entry_details[0]
        self.assertEqual(entry["source_entry_hash"], expected_entry.source_entry_hash)
        self.assertEqual(detail["source_detail_hash"], expected_detail.source_detail_hash)
        self.assertEqual(
            detail[_STRUCTURED_EVIDENCE_KEY],
            self.base_projection,
        )
        first_line, second_line = self.line_source_contract._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.first_line_projection)
        self.assertEqual(second_line.get(_ADJUSTMENT_KEY), [_ADJT, _FEES])
        self._assert_second_line_stable_without_adjustments(second_line)
        self._assert_exact_persisted_transaction(entry, detail)
        self._assert_sibling_entry(entries[1], self.base_statement.entries[1])

    def test_rejected_adjustment_contraction_leaves_no_evidence_residue(self) -> None:
        """Reject one adjustment removal without relational or raw-artifact residue."""
        restricted_owner = (
            self.parent.parent.parent.parent.parent.parent.parent._ancestor_with(
                "_restricted_bank_statement_runtime_url",
                "_tenant_statement_rows",
            )
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-adjustment-contraction-base",
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
                    "line-adjustment-contraction-changed",
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

    def test_buyer_read_keeps_only_the_source_retained_adjustment(self) -> None:
        """Expose ADJT only after the later FEES member disappears from source."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-adjustment-contraction-lookup",
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
        statement, entries = self._lookup(str(accepted["bank_statement_record_id"]))
        self._assert_persisted_statement_identity(statement, self.changed_statement)

        entry = entries[0]
        detail = entry["entry_details"][0]
        changed_entry = self.changed_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]
        self.assertEqual(entry["source_entry_hash"], changed_entry.source_entry_hash)
        self.assertEqual(detail["source_detail_hash"], changed_detail.source_detail_hash)
        self.assertEqual(
            detail[_STRUCTURED_EVIDENCE_KEY],
            self.changed_projection,
        )

        first_line, second_line = self.line_source_contract._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.first_line_projection)
        self.assertEqual(second_line.get(_ADJUSTMENT_KEY), [_ADJT])
        self._assert_second_line_stable_without_adjustments(second_line)
        self._assert_exact_persisted_transaction(entry, detail)
        self._assert_sibling_entry(entries[1], self.changed_statement.entries[1])

    def _projection_with_adjustments(
        self,
        projection: list[dict[str, object]],
        adjustments: list[dict[str, str]],
    ) -> list[dict[str, object]]:
        """Attach one exact source-ordered adjustment population to line two."""
        changed = deepcopy(projection)
        first_line, second_line = self.line_source_contract._line_details(changed)
        if _ADJUSTMENT_KEY in first_line or _ADJUSTMENT_KEY in second_line:
            raise AssertionError(
                "#123 projection must begin with no line adjustments"
            )
        second_line[_ADJUSTMENT_KEY] = deepcopy(adjustments)
        return changed

    def _second_line_adjustment_blocks(self, payload: bytes) -> list[str]:
        """Return complete direct adjustment blocks from line two's Amount group."""
        segment, _, _ = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self.line_source_contract._direct_amount_bounds(
            segment
        )
        blocks: list[str] = []
        index = amount_start + 1
        while index < amount_end:
            if lines[index].strip() != "<AdjstmntAmtAndRsn>":
                index += 1
                continue
            outer_indent = lines[index][
                : len(lines[index]) - len(lines[index].lstrip(" "))
            ]
            close_index = index + 1
            while close_index < amount_end:
                if (
                    lines[close_index].strip() == "</AdjstmntAmtAndRsn>"
                    and lines[close_index].startswith(outer_indent)
                    and len(lines[close_index])
                    - len(lines[close_index].lstrip(" "))
                    == len(outer_indent)
                ):
                    break
                close_index += 1
            if close_index >= amount_end:
                raise AssertionError(
                    "direct adjustment block must have a matching close"
                )
            blocks.append("".join(lines[index : close_index + 1]))
            index = close_index + 1
        return blocks

    def _adjustment_source_block(
        self,
        payload: bytes,
        adjustment: dict[str, str],
    ) -> str:
        """Build one adjustment with indentation derived from retained TaxAmt."""
        segment, _, _ = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self.line_source_contract._direct_amount_bounds(
            segment
        )
        tax_starts = [
            index
            for index in range(amount_start + 1, amount_end)
            if lines[index].strip() == "<TaxAmt>"
        ]
        if len(tax_starts) != 1:
            raise AssertionError(
                "#123 changed source must retain exactly one direct TaxAmt"
            )
        tax_start = tax_starts[0]
        outer_indent = lines[tax_start][
            : len(lines[tax_start]) - len(lines[tax_start].lstrip(" "))
        ]
        inner_candidates = [
            lines[index]
            for index in range(tax_start + 1, amount_end)
            if lines[index].strip().startswith("<Tp>")
            or lines[index].strip().startswith("<Amt Ccy=")
        ]
        if not inner_candidates:
            raise AssertionError("retained TaxAmt must expose child indentation")
        inner_line = inner_candidates[0]
        inner_indent = inner_line[
            : len(inner_line) - len(inner_line.lstrip(" "))
        ]
        if not inner_indent.startswith(outer_indent) or len(inner_indent) <= len(
            outer_indent
        ):
            raise AssertionError(
                "adjustment child indentation must be deeper than direct Amount child"
            )
        return (
            f"{outer_indent}<AdjstmntAmtAndRsn>\n"
            f'<Amt Ccy="{adjustment["currency_code"]}">' if False else ""
        ) + (
            f'{inner_indent}<Amt Ccy="{adjustment["currency_code"]}">'
            f'{Decimal(adjustment["amount"]):.2f}</Amt>\n'
            f'{inner_indent}<CdtDbtInd>{adjustment["credit_debit_code"]}'
            f"</CdtDbtInd>\n"
            f'{inner_indent}<Rsn>{adjustment["reason_code"]}</Rsn>\n'
            f'{inner_indent}<AddtlInf>{adjustment["additional_information"]}'
            f"</AddtlInf>\n"
            f"{outer_indent}</AdjstmntAmtAndRsn>\n"
        )

    def _seed_second_line_adjustment(
        self,
        payload: bytes,
        adjustment: dict[str, str],
    ) -> bytes:
        """Insert one complete adjustment after taxes and before remitted amount."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        source_block = self._adjustment_source_block(payload, adjustment)
        if adjustment["reason_code"] in {
            self._adjustment_identity(block)
            for block in self._second_line_adjustment_blocks(payload)
        }:
            raise AssertionError(
                f'line two already contains {adjustment["reason_code"]} adjustment'
            )

        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self.line_source_contract._direct_amount_bounds(
            segment
        )
        remitted_indexes = [
            index
            for index in range(amount_start + 1, amount_end)
            if lines[index].lstrip().startswith("<RmtdAmt ")
        ]
        if len(remitted_indexes) > 1:
            raise AssertionError("line two may contain at most one direct RmtdAmt")
        insert_index = remitted_indexes[0] if remitted_indexes else amount_end
        lines.insert(insert_index, source_block)
        seeded_segment = "".join(lines)
        seeded = (
            text[:segment_start] + seeded_segment + text[segment_end:]
        ).encode("utf-8")
        reasons = [
            self._adjustment_identity(block)
            for block in self._second_line_adjustment_blocks(seeded)
        ]
        if adjustment["reason_code"] not in reasons:
            raise AssertionError(
                "seeded adjustment must remain in line-two direct Amount group"
            )
        return seeded

    def _remove_later_second_line_adjustment(self, payload: bytes) -> bytes:
        """Remove only the exact later FEES / 50-KRW adjustment block."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        blocks = self._second_line_adjustment_blocks(payload)
        if [self._adjustment_identity(block) for block in blocks] != [
            "ADJT",
            "FEES",
        ]:
            raise AssertionError(
                "seeded line must contain source-ordered ADJT then FEES adjustments"
            )
        source_block = blocks[1]
        if segment.count(source_block) != 1:
            raise AssertionError(
                "later complete adjustment block must occur exactly once"
            )
        changed_segment = segment.replace(source_block, "", 1)
        changed = (
            text[:segment_start] + changed_segment + text[segment_end:]
        ).encode("utf-8")
        remaining = self._second_line_adjustment_blocks(changed)
        if [self._adjustment_identity(block) for block in remaining] != ["ADJT"]:
            raise AssertionError(
                "adjustment contraction must retain exactly ADJT / 200 KRW"
            )
        return changed

    def _restore_later_second_line_adjustment(self, payload: bytes) -> bytes:
        """Restore the exact later FEES / 50-KRW member in schema order."""
        blocks = self._second_line_adjustment_blocks(payload)
        if [self._adjustment_identity(block) for block in blocks] != ["ADJT"]:
            raise AssertionError(
                "contracted source must retain only ADJT / 200 KRW"
            )
        return self._seed_second_line_adjustment(payload, _FEES)

    @staticmethod
    def _adjustment_identity(block: str) -> str:
        """Identify only the two canonical complete adjustment tuples."""
        markers = {
            "ADJT": (
                '<Amt Ccy="KRW">200.00</Amt>',
                "<CdtDbtInd>CRDT</CdtDbtInd>",
                "<Rsn>ADJT</Rsn>",
                "<AddtlInf>Contract true-up</AddtlInf>",
            ),
            "FEES": (
                '<Amt Ccy="KRW">50.00</Amt>',
                "<CdtDbtInd>DBIT</CdtDbtInd>",
                "<Rsn>FEES</Rsn>",
                "<AddtlInf>Processing fee</AddtlInf>",
            ),
        }
        matches = [
            reason
            for reason, expected in markers.items()
            if all(marker in block for marker in expected)
        ]
        if len(matches) != 1:
            raise AssertionError(
                "adjustment block must match exactly one canonical tuple"
            )
        return matches[0]

    def _lookup(
        self,
        record_id: str,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Read one accepted statement and its ordered entry population."""
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
        return statement, entries

    def _assert_persisted_statement_identity(
        self,
        statement: dict[str, object],
        expected: object,
    ) -> None:
        """Bind persisted raw and normalized statement hashes to parsed evidence."""
        self.assertEqual(
            statement["source_artifact_hash"],
            expected.source_artifact_hash,
        )
        self.assertEqual(
            statement["normalized_payload_hash"],
            expected.normalized_payload_hash,
        )

    def _assert_second_line_stable_without_adjustments(
        self,
        second_line: dict[str, object],
    ) -> None:
        """Keep inherited Tax/Discount and omission evidence outside the target stable."""
        stable = deepcopy(second_line)
        stable.pop(_ADJUSTMENT_KEY)
        self.assertEqual(stable, self.second_line_without_adjustments)
        self.assertEqual(
            second_line.get("tax_amounts"),
            [deepcopy(repeated_tax_contract._STAT)],
        )
        self.assertEqual(
            second_line.get("discount_applied_amounts"),
            [{"type_code": "APDS", "amount": "100", "currency_code": "KRW"}],
        )
        for absent_key in (
            "credit_note_amount",
            "due_payable_amount",
            "remitted_amount",
            "description",
        ):
            self.assertNotIn(absent_key, second_line)

    def _assert_sibling_entry(
        self,
        persisted: dict[str, object],
        expected: object,
    ) -> None:
        """Keep the untouched sibling entry and every detail exact."""
        self.assertEqual(persisted["source_entry_hash"], expected.source_entry_hash)
        self.assertEqual(
            Decimal(str(persisted["entry_amount"])),
            expected.entry_amount,
        )
        self.assertEqual(
            persisted["entry_currency_code"],
            expected.entry_currency_code,
        )
        self.assertEqual(
            len(persisted["entry_details"]),
            len(expected.entry_details),
        )
        for persisted_detail, expected_detail in zip(
            persisted["entry_details"],
            expected.entry_details,
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

    @staticmethod
    def _assert_exact_persisted_transaction(
        entry: dict[str, object],
        detail: dict[str, object],
    ) -> None:
        """Keep bank adjustment evidence separate from authoritative transaction truth."""
        if Decimal(str(entry["entry_amount"])) != Decimal("25000.00"):
            raise AssertionError("entry amount must remain exactly 25000.00")
        if entry["entry_currency_code"] != "KRW":
            raise AssertionError("entry currency must remain KRW")
        if Decimal(str(detail["detail_amount"])) != Decimal("25000.00"):
            raise AssertionError("detail amount must remain exactly 25000.00")
        if detail["detail_currency_code"] != "KRW":
            raise AssertionError("detail currency must remain KRW")


if __name__ == "__main__":
    unittest.main()
