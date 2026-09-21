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
    """Preserve a 2-to-1 repeated adjustment contraction on one source line."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-tax contraction fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Seed ADJT then FEES on #123's retained line-two Amount group."""
        self.parent = _PARENT_TEST(
            "test_later_tax_removal_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract
        self.line_source_contract = self.parent.parent

        self.inherited_payload = self.parent.changed_payload
        self.inherited_projection = deepcopy(self.parent.changed_projection)
        first_inherited, second_inherited = self.line_source_contract._line_details(
            self.inherited_projection
        )
        if second_inherited.get("tax_amounts") != [
            deepcopy(repeated_tax_contract._STAT)
        ]:
            raise AssertionError("#123 must retain exactly STAT / 950 KRW")
        if second_inherited.get("discount_applied_amounts") != [
            {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
        ]:
            raise AssertionError("#123 must retain exactly APDS / 100 KRW")
        if _ADJUSTMENT_KEY in first_inherited or _ADJUSTMENT_KEY in second_inherited:
            raise AssertionError("#123 must begin with no line adjustments")

        self.base_payload = self._seed_adjustment(self.inherited_payload, "ADJT")
        self.base_payload = self._seed_adjustment(self.base_payload, "FEES")
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = self._projection_with_adjustments(
            self.inherited_projection,
            [_ADJT, _FEES],
        )

        self.changed_payload, self.removed_fees_block = self._remove_later_adjustment(
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
        if self._restore_removed_adjustment(
            self.changed_payload,
            self.removed_fees_block,
        ) != self.base_payload:
            raise AssertionError(
                "exact removed adjustment bytes must restore the two-member source"
            )

        self.first_line_projection, second_line = (
            self.line_source_contract._line_details(self.changed_projection)
        )
        self.first_line_projection = deepcopy(self.first_line_projection)
        self.second_line_without_adjustments = deepcopy(second_line)
        self.second_line_without_adjustments.pop(_ADJUSTMENT_KEY)

    def test_adjustment_contraction_is_material_to_each_evidence_hash(self) -> None:
        """Bind the exact 2-to-1 contraction to raw and canonical identity."""
        self.assertEqual(
            self._restore_removed_adjustment(
                self.changed_payload,
                self.removed_fees_block,
            ),
            self.base_payload,
        )
        _NORMALIZATION_PARENT_TEST._assert_non_structured_normalization_unchanged(self)

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

    def test_two_adjustment_baseline_reads_back_in_source_order(self) -> None:
        """Persist ADJT then FEES so repeated-member truncation cannot false-GREEN."""
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
        statement, entries = self._lookup(
            str(accepted["bank_statement_record_id"]),
            self.base_statement,
        )
        self._assert_statement_hashes(statement, self.base_statement)
        self._assert_primary_entry(
            entries[0],
            self.base_statement.entries[0],
            self.base_projection,
            [_ADJT, _FEES],
        )
        self._assert_sibling(entries[1], self.base_statement.entries[1])

    def test_rejected_adjustment_contraction_leaves_no_evidence_residue(self) -> None:
        """Reject FEES removal without relational or object-store residue."""
        restricted_owner = self._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
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

    def test_contracted_adjustment_reads_back_only_retained_adjt(self) -> None:
        """Persist the changed source and expose only ADJT on the exact line."""
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
        statement, entries = self._lookup(
            str(accepted["bank_statement_record_id"]),
            self.changed_statement,
        )
        self._assert_statement_hashes(statement, self.changed_statement)
        self._assert_primary_entry(
            entries[0],
            self.changed_statement.entries[0],
            self.changed_projection,
            [_ADJT],
        )
        self._assert_sibling(entries[1], self.changed_statement.entries[1])

    def _projection_with_adjustments(
        self,
        projection: list[dict[str, object]],
        adjustments: list[dict[str, str]],
    ) -> list[dict[str, object]]:
        """Attach one source-ordered adjustment population to line two only."""
        changed = deepcopy(projection)
        first_line, second_line = self.line_source_contract._line_details(changed)
        if _ADJUSTMENT_KEY in first_line or _ADJUSTMENT_KEY in second_line:
            raise AssertionError("inherited projection already contains adjustments")
        second_line[_ADJUSTMENT_KEY] = deepcopy(adjustments)
        return changed

    def _adjustment_blocks(self, payload: bytes) -> list[str]:
        """Return complete direct adjustment blocks from line two's Amount."""
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
            outer_indent = self._indent(lines[index])
            close_index = index + 1
            while close_index < amount_end:
                if (
                    lines[close_index].strip() == "</AdjstmntAmtAndRsn>"
                    and self._indent(lines[close_index]) == outer_indent
                ):
                    break
                close_index += 1
            if close_index >= amount_end:
                raise AssertionError("direct adjustment block has no matching close")
            blocks.append("".join(lines[index : close_index + 1]))
            index = close_index + 1
        return blocks

    def _seed_adjustment(self, payload: bytes, reason: str) -> bytes:
        """Insert one complete adjustment after tax and before remitted amount."""
        source_block = self._adjustment_source_block(payload, reason)
        existing = [
            self._adjustment_identity(block)
            for block in self._adjustment_blocks(payload)
        ]
        if reason in existing:
            raise AssertionError(f"line two already contains {reason} adjustment")

        text = payload.decode("utf-8")
        segment, start, end = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
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
            raise AssertionError("line two may have at most one direct RmtdAmt")
        insert_index = remitted_indexes[0] if remitted_indexes else amount_end
        lines.insert(insert_index, source_block)
        seeded = (text[:start] + "".join(lines) + text[end:]).encode("utf-8")
        reasons = [
            self._adjustment_identity(block)
            for block in self._adjustment_blocks(seeded)
        ]
        if reason not in reasons:
            raise AssertionError("seeded adjustment escaped the direct Amount group")
        return seeded

    def _adjustment_source_block(self, payload: bytes, reason: str) -> str:
        """Build a canonical tuple using indentation from the retained TaxAmt."""
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
            raise AssertionError("line two must retain exactly one direct TaxAmt")
        tax_start = tax_starts[0]
        outer_indent = self._indent(lines[tax_start])
        inner_candidates = [
            lines[index]
            for index in range(tax_start + 1, amount_end)
            if lines[index].strip().startswith("<Tp>")
            or lines[index].strip().startswith("<Amt Ccy=")
        ]
        if not inner_candidates:
            raise AssertionError("retained TaxAmt must expose child indentation")
        inner_indent = self._indent(inner_candidates[0])
        if not inner_indent.startswith(outer_indent) or len(inner_indent) <= len(
            outer_indent
        ):
            raise AssertionError("adjustment child indentation is not deeper")

        if reason == "ADJT":
            adjustment = _ADJT
        elif reason == "FEES":
            adjustment = _FEES
        else:
            raise AssertionError(f"unsupported adjustment fixture reason {reason!r}")
        amount = f'{Decimal(adjustment["amount"]):.2f}'
        return (
            f"{outer_indent}<AdjstmntAmtAndRsn>\n"
            f'{inner_indent}<Amt Ccy="{adjustment["currency_code"]}">{amount}</Amt>\n'
            f'{inner_indent}<CdtDbtInd>{adjustment["credit_debit_code"]}</CdtDbtInd>\n'
            f'{inner_indent}<Rsn>{adjustment["reason_code"]}</Rsn>\n'
            f'{inner_indent}<AddtlInf>{adjustment["additional_information"]}</AddtlInf>\n'
            f"{outer_indent}</AdjstmntAmtAndRsn>\n"
        )

    def _remove_later_adjustment(self, payload: bytes) -> tuple[bytes, str]:
        """Remove only the later complete FEES block and return its exact bytes."""
        text = payload.decode("utf-8")
        segment, start, end = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        blocks = self._adjustment_blocks(payload)
        if [self._adjustment_identity(block) for block in blocks] != [
            "ADJT",
            "FEES",
        ]:
            raise AssertionError("expected source order ADJT then FEES")
        removed = blocks[1]
        if segment.count(removed) != 1:
            raise AssertionError("later FEES block must occur exactly once")
        changed = (
            text[:start] + segment.replace(removed, "", 1) + text[end:]
        ).encode("utf-8")
        if [
            self._adjustment_identity(block)
            for block in self._adjustment_blocks(changed)
        ] != ["ADJT"]:
            raise AssertionError("contraction must retain only ADJT")
        return changed, removed

    def _restore_removed_adjustment(self, payload: bytes, removed: str) -> bytes:
        """Reinsert the exact removed bytes immediately before direct Amount close."""
        if [
            self._adjustment_identity(block)
            for block in self._adjustment_blocks(payload)
        ] != ["ADJT"]:
            raise AssertionError("contracted source must retain only ADJT")
        text = payload.decode("utf-8")
        segment, start, end = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        lines = segment.splitlines(keepends=True)
        _, amount_end = self.line_source_contract._direct_amount_bounds(segment)
        lines.insert(amount_end, removed)
        return (text[:start] + "".join(lines) + text[end:]).encode("utf-8")

    @staticmethod
    def _adjustment_identity(block: str) -> str:
        """Identify only the two complete tuples used by this contract."""
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
            raise AssertionError("adjustment block must match exactly one tuple")
        return matches[0]

    @staticmethod
    def _indent(line: str) -> str:
        """Return leading spaces from one fixture line."""
        return line[: len(line) - len(line.lstrip(" "))]

    def _ancestor_with(self, *names: str) -> object:
        """Find the nearest inherited test owner exposing all requested helpers."""
        current: object | None = self.parent
        while current is not None:
            if all(hasattr(current, name) for name in names):
                return current
            current = getattr(current, "parent", None)
        raise AssertionError(f"no inherited owner exposes helpers {names!r}")

    def _lookup(
        self,
        record_id: str,
        expected_statement: object,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Read one statement and assert complete entry cardinality."""
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
        self.assertEqual(len(entries), len(expected_statement.entries))
        return statement, entries

    def _assert_statement_hashes(
        self,
        persisted: dict[str, object],
        expected: object,
    ) -> None:
        """Bind persisted raw and normalized hashes to parsed source evidence."""
        self.assertEqual(
            persisted["source_artifact_hash"],
            expected.source_artifact_hash,
        )
        self.assertEqual(
            persisted["normalized_payload_hash"],
            expected.normalized_payload_hash,
        )

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        expected_adjustments: list[dict[str, str]],
    ) -> None:
        """Assert exact hashes, projection, sibling fields, and transaction truth."""
        persisted_detail = persisted_entry["entry_details"][0]
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
        self.assertEqual(second_line.get(_ADJUSTMENT_KEY), expected_adjustments)

        stable_second = deepcopy(second_line)
        stable_second.pop(_ADJUSTMENT_KEY)
        self.assertEqual(stable_second, self.second_line_without_adjustments)
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

    def _assert_sibling(self, persisted: dict[str, object], expected: object) -> None:
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


if __name__ == "__main__":
    unittest.main()
