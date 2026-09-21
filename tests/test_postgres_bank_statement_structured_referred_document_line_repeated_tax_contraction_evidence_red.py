"""REDs for source-faithful contraction of repeated line tax evidence."""

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
    test_postgres_bank_statement_structured_referred_document_line_optional_credit_note_absence_evidence_red
    as optional_credit_note_contract,
)

_PARENT_TEST = (
    optional_credit_note_contract.
    BankStatementStructuredLineOptionalCreditNoteAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = optional_credit_note_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = optional_credit_note_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_TAX_KEY = "tax_amounts"
_STAT = {"type_code": "STAT", "amount": "950", "currency_code": "KRW"}
_LOCL = {"type_code": "LOCL", "amount": "50", "currency_code": "KRW"}


class BankStatementStructuredLineRepeatedTaxContractionEvidenceRedTests(
    unittest.TestCase
):
    """Preserve a 2-to-1 repeated TaxAmt contraction on the exact source line."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-credit-note-absence fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Seed two line-two taxes, then remove only the later LOCL member."""
        self.parent = _PARENT_TEST(
            "test_credit_note_absence_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # #120 changed source keeps line-one credit-note evidence but intentionally
        # omits line two's CdtNoteAmt. It also inherits APDS-only Amount evidence
        # after DuePyblAmt/RmtdAmt/Description and optional Id Type disappeared.
        self.inherited_payload = self.parent.changed_payload
        self.inherited_projection = deepcopy(self.parent.changed_projection)
        first_inherited, second_inherited = self.parent._line_details(
            self.inherited_projection
        )
        if first_inherited.get("credit_note_amount") != {
            "amount": "1200",
            "currency_code": "KRW",
        }:
            raise AssertionError(
                "#120 changed source must retain the line-one 1200-KRW credit note"
            )
        if "credit_note_amount" in second_inherited:
            raise AssertionError(
                "#120 changed source must keep line-two credit-note evidence absent"
            )
        if second_inherited.get("discount_applied_amounts") != [
            {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
        ]:
            raise AssertionError(
                "#120 changed source must retain exactly APDS / 100 KRW"
            )
        for absent_key in ("due_payable_amount", "remitted_amount", "description"):
            if absent_key in second_inherited:
                raise AssertionError(
                    f"#120 second line must keep {absent_key!r} absent"
                )
        if _TAX_KEY in second_inherited:
            raise AssertionError("#120 second line must begin with no tax members")

        self.base_payload = self._seed_second_line_tax(
            self.inherited_payload,
            "STAT",
            "950.00",
        )
        self.base_payload = self._seed_second_line_tax(
            self.base_payload,
            "LOCL",
            "50.00",
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = self._projection_with_taxes(
            self.inherited_projection,
            [_STAT, _LOCL],
        )

        self.changed_payload = self._remove_later_second_line_tax(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_with_taxes(
            self.inherited_projection,
            [_STAT],
        )
        if self._restore_later_second_line_tax(self.changed_payload) != self.base_payload:
            raise AssertionError(
                "later TaxAmt restoration must recover the exact two-tax source bytes"
            )
        self.first_line_projection, second_line = self.parent._line_details(
            self.changed_projection
        )
        self.first_line_projection = deepcopy(self.first_line_projection)
        self.second_line_without_taxes = deepcopy(second_line)
        self.second_line_without_taxes.pop(_TAX_KEY)

    def test_later_tax_removal_is_material_to_each_evidence_hash(self) -> None:
        """Bind repeated TaxAmt contraction through raw and canonical evidence identity."""
        self.assertEqual(
            self._restore_later_second_line_tax(self.changed_payload),
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

    def test_rejected_tax_contraction_leaves_no_evidence_residue(self) -> None:
        """Reject one TaxAmt removal without relational or raw-artifact residue."""
        restricted_owner = (
            self.parent.parent.parent.parent.parent.parent._ancestor_with(
                "_restricted_bank_statement_runtime_url",
                "_tenant_statement_rows",
            )
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-tax-contraction-base",
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
                    "line-tax-contraction-changed",
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

    def test_buyer_read_keeps_only_the_source_retained_tax(self) -> None:
        """Expose STAT only after the later LOCL TaxAmt disappears from source."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-tax-contraction-lookup",
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
        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.changed_projection,
        )
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

        first_line, second_line = self.parent._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.first_line_projection)
        taxes = second_line.get(_TAX_KEY)
        self.assertEqual(taxes, [_STAT])
        stable_second_line = deepcopy(second_line)
        stable_second_line.pop(_TAX_KEY)
        self.assertEqual(stable_second_line, self.second_line_without_taxes)
        self.assertEqual(
            first_line.get("credit_note_amount"),
            {"amount": "1200", "currency_code": "KRW"},
        )
        self.assertNotIn("credit_note_amount", second_line)
        self.assertEqual(
            second_line.get("discount_applied_amounts"),
            [{"type_code": "APDS", "amount": "100", "currency_code": "KRW"}],
        )
        self.assertNotIn("due_payable_amount", second_line)
        self.assertNotIn("remitted_amount", second_line)
        self.assertNotIn("description", second_line)
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_with_taxes(
        self,
        projection: list[dict[str, object]],
        taxes: list[dict[str, str]],
    ) -> list[dict[str, object]]:
        """Attach an exact source-ordered tax population to line two only."""
        changed = deepcopy(projection)
        first_line, second_line = self.parent._line_details(changed)
        if _TAX_KEY in first_line or _TAX_KEY in second_line:
            raise AssertionError("#120 projection must begin with no line taxes")
        second_line[_TAX_KEY] = deepcopy(taxes)
        return changed

    def _second_line_tax_blocks(self, payload: bytes) -> list[str]:
        """Return complete direct TaxAmt blocks from line two's direct Amount group."""
        segment, _, _ = self.parent._line_segment(payload, "Stockitem2")
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self.parent._direct_amount_bounds(segment)
        blocks: list[str] = []
        index = amount_start + 1
        while index < amount_end:
            if lines[index].strip() != "<TaxAmt>":
                index += 1
                continue
            outer_indent = lines[index][: len(lines[index]) - len(lines[index].lstrip(" "))]
            close_index = index + 1
            while close_index < amount_end:
                if (
                    lines[close_index].strip() == "</TaxAmt>"
                    and lines[close_index].startswith(outer_indent)
                    and len(lines[close_index]) - len(lines[close_index].lstrip(" "))
                    == len(outer_indent)
                ):
                    break
                close_index += 1
            if close_index >= amount_end:
                raise AssertionError("direct TaxAmt block must have a matching close")
            blocks.append("".join(lines[index : close_index + 1]))
            index = close_index + 1
        return blocks

    def _tax_source_block(
        self,
        payload: bytes,
        type_code: str,
        amount: str,
    ) -> str:
        """Build one tax block from the fixture's retained direct-child indentation."""
        segment, _, _ = self.parent._line_segment(payload, "Stockitem2")
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self.parent._direct_amount_bounds(segment)
        discount_starts = [
            index
            for index in range(amount_start + 1, amount_end)
            if lines[index].strip() == "<DscntApldAmt>"
        ]
        if len(discount_starts) != 1:
            raise AssertionError(
                "line two must retain exactly one direct Discount Applied Amount"
            )
        discount_start = discount_starts[0]
        outer_indent = lines[discount_start][
            : len(lines[discount_start]) - len(lines[discount_start].lstrip(" "))
        ]
        inner_candidates = [
            lines[index]
            for index in range(discount_start + 1, amount_end)
            if lines[index].strip().startswith("<Tp>")
            or lines[index].strip().startswith("<Amt Ccy=")
        ]
        if not inner_candidates:
            raise AssertionError("discount fixture must expose direct-child indentation")
        inner_line = inner_candidates[0]
        inner_indent = inner_line[
            : len(inner_line) - len(inner_line.lstrip(" "))
        ]
        if not inner_indent.startswith(outer_indent) or len(inner_indent) <= len(
            outer_indent
        ):
            raise AssertionError(
                "tax inner indentation must be deeper than the direct Amount child"
            )
        return (
            f"{outer_indent}<TaxAmt>\n"
            f"{inner_indent}<Tp><Cd>{type_code}</Cd></Tp>\n"
            f'{inner_indent}<Amt Ccy="KRW">{amount}</Amt>\n'
            f"{outer_indent}</TaxAmt>\n"
        )

    def _seed_second_line_tax(
        self,
        payload: bytes,
        type_code: str,
        amount: str,
    ) -> bytes:
        """Insert one complete tax after discounts/credit note in schema order."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent._line_segment(
            payload,
            "Stockitem2",
        )
        source_block = self._tax_source_block(payload, type_code, amount)
        if source_block in segment or f"<Cd>{type_code}</Cd>" in "".join(
            self._second_line_tax_blocks(payload)
        ):
            raise AssertionError(f"line two must not already contain {type_code} tax")

        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self.parent._direct_amount_bounds(segment)
        later_indexes = [
            index
            for index in range(amount_start + 1, amount_end)
            if (
                lines[index].strip() == "<AdjstmntAmtAndRsn>"
                or lines[index].lstrip().startswith("<RmtdAmt ")
            )
        ]
        insert_index = later_indexes[0] if later_indexes else amount_end
        lines.insert(insert_index, source_block)
        seeded_segment = "".join(lines)
        seeded = (
            text[:segment_start] + seeded_segment + text[segment_end:]
        ).encode("utf-8")
        blocks = self._second_line_tax_blocks(seeded)
        expected_codes = [
            code
            for code in ("STAT", "LOCL")
            if f"<Cd>{code}</Cd>" in "".join(blocks)
        ]
        if type_code not in expected_codes:
            raise AssertionError("seeded tax must remain inside line-two direct Amount")
        return seeded

    def _remove_later_second_line_tax(self, payload: bytes) -> bytes:
        """Remove only the exact later LOCL / 50-KRW tax source block."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent._line_segment(
            payload,
            "Stockitem2",
        )
        blocks = self._second_line_tax_blocks(payload)
        if len(blocks) != 2:
            raise AssertionError("seeded line must contain exactly two tax members")
        if "<Cd>STAT</Cd>" not in blocks[0] or '">950.00</Amt>' not in blocks[0]:
            raise AssertionError("first tax must remain STAT / 950 KRW")
        if "<Cd>LOCL</Cd>" not in blocks[1] or '">50.00</Amt>' not in blocks[1]:
            raise AssertionError("later tax must be LOCL / 50 KRW")
        source_block = blocks[1]
        if segment.count(source_block) != 1:
            raise AssertionError("later complete TaxAmt block must occur exactly once")
        changed_segment = segment.replace(source_block, "", 1)
        changed = (
            text[:segment_start] + changed_segment + text[segment_end:]
        ).encode("utf-8")
        remaining = self._second_line_tax_blocks(changed)
        if len(remaining) != 1 or "<Cd>STAT</Cd>" not in remaining[0]:
            raise AssertionError("tax contraction must retain exactly STAT / 950 KRW")
        return changed

    def _restore_later_second_line_tax(self, payload: bytes) -> bytes:
        """Restore the exact later LOCL / 50-KRW member in schema order."""
        blocks = self._second_line_tax_blocks(payload)
        if len(blocks) != 1 or "<Cd>STAT</Cd>" not in blocks[0]:
            raise AssertionError("contracted source must retain only STAT / 950 KRW")
        return self._seed_second_line_tax(payload, "LOCL", "50.00")


if __name__ == "__main__":
    unittest.main()
