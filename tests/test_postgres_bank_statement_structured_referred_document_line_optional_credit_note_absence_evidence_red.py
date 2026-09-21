"""REDs for source-faithful optional line credit-note absence."""

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
    test_postgres_bank_statement_structured_referred_document_line_repeated_discount_contraction_evidence_red
    as discount_contraction_contract,
)

_PARENT_TEST = (
    discount_contraction_contract.
    BankStatementStructuredLineRepeatedDiscountContractionEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = discount_contraction_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = discount_contraction_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_CREDIT_NOTE_KEY = "credit_note_amount"
_FIRST_CREDIT_NOTE = {"amount": "1200", "currency_code": "KRW"}
_SECOND_CREDIT_NOTE = {"amount": "800", "currency_code": "KRW"}


class BankStatementStructuredLineOptionalCreditNoteAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve one line's credit-note absence while a sibling retains evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-discount contraction fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Seed distinct sibling credit notes, then omit only line two's member."""
        self.parent = _PARENT_TEST(
            "test_later_discount_removal_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # #119 changed source is the exact #118 APDS-only state after the seeded
        # STDS member contracts away. Direct Amount stays present on line two while
        # DuePyblAmt/RmtdAmt/Description and inherited optional Id Type stay absent.
        self.inherited_payload = self.parent.changed_payload
        self.inherited_projection = deepcopy(self.parent.changed_projection)
        first_inherited, second_inherited = self._line_details(
            self.inherited_projection
        )
        if second_inherited.get("discount_applied_amounts") != [
            {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
        ]:
            raise AssertionError(
                "#119 changed source must retain exactly APDS / 100 KRW"
            )
        for line in (first_inherited, second_inherited):
            if _CREDIT_NOTE_KEY in line:
                raise AssertionError(
                    "#119 projection must not already contain line credit notes"
                )
        for absent_key in ("due_payable_amount", "remitted_amount", "description"):
            if absent_key in second_inherited:
                raise AssertionError(
                    f"#119 second line must keep {absent_key!r} absent"
                )

        # A distinct first-line credit note remains in both compared payloads.
        # This makes line-two absence causal against stale/cross-line copying.
        self.changed_payload = self._seed_credit_note(
            self.inherited_payload,
            "Stockitem1",
            "1200.00",
        )
        self.base_payload = self._seed_credit_note(
            self.changed_payload,
            "Stockitem2",
            "800.00",
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.changed_projection = self._projection_with_first_credit_note(
            self.inherited_projection
        )
        self.base_projection = self._projection_with_both_credit_notes(
            self.inherited_projection
        )
        first_line, second_line = self._line_details(self.changed_projection)
        self.first_line_projection = deepcopy(first_line)
        self.second_line_without_credit_note = deepcopy(second_line)

        omitted = self._remove_second_line_credit_note(self.base_payload)
        if omitted != self.changed_payload:
            raise AssertionError(
                "line-two credit-note omission must preserve the line-one sibling"
            )
        if self._projection_without_second_credit_note() != self.changed_projection:
            raise AssertionError(
                "projection contraction must preserve only line-one credit-note evidence"
            )

    def test_credit_note_absence_is_material_to_each_evidence_hash(self) -> None:
        """Bind optional line-two credit-note omission through evidence identity."""
        self.assertEqual(
            self._restore_second_line_credit_note(self.changed_payload),
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
        self.line_contract._assert_exact_transaction_amount(
            changed_entry,
            changed_detail,
        )

    def test_rejected_credit_note_omission_leaves_no_evidence_residue(self) -> None:
        """Reject optional-child omission without relational or raw-artifact residue."""
        restricted_owner = self.parent.parent.parent.parent.parent._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-credit-note-absence-base",
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
                    "line-credit-note-absence-changed",
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

    def test_buyer_read_keeps_absence_bound_to_the_second_source_line(self) -> None:
        """Retain line-one credit note without manufacturing one on line two."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-credit-note-absence-lookup",
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
        self.assertEqual(
            entry["source_entry_hash"],
            changed_entry.source_entry_hash,
        )
        self.assertEqual(
            detail["source_detail_hash"],
            changed_detail.source_detail_hash,
        )
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

        first_line, second_line = self._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.first_line_projection)
        self.assertEqual(first_line.get(_CREDIT_NOTE_KEY), _FIRST_CREDIT_NOTE)
        self.assertNotIn(_CREDIT_NOTE_KEY, second_line)
        self.assertEqual(second_line, self.second_line_without_credit_note)
        self.assertEqual(
            second_line.get("discount_applied_amounts"),
            [{"type_code": "APDS", "amount": "100", "currency_code": "KRW"}],
        )
        self.assertNotIn("due_payable_amount", second_line)
        self.assertNotIn("remitted_amount", second_line)
        self.assertNotIn("description", second_line)
        self.assertEqual(
            Decimal(str(entry["entry_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(
            Decimal(str(detail["detail_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _line_details(
        self,
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return the canonical two line-detail mappings without copying them."""
        if len(projection) != 1:
            raise AssertionError(
                "canonical projection must contain one referred document"
            )
        line_details = projection[0].get("line_details")
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError(
                "canonical projection must contain exactly two line details"
            )
        first_line, second_line = line_details
        if not isinstance(first_line, dict) or not isinstance(second_line, dict):
            raise AssertionError("line details must remain mapping objects")
        return first_line, second_line

    def _projection_with_first_credit_note(
        self,
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Seed only the line-one credit note used as a non-copy sibling."""
        changed = deepcopy(projection)
        first_line, second_line = self._line_details(changed)
        if _CREDIT_NOTE_KEY in first_line or _CREDIT_NOTE_KEY in second_line:
            raise AssertionError("#119 projection must begin with no credit notes")
        first_line[_CREDIT_NOTE_KEY] = deepcopy(_FIRST_CREDIT_NOTE)
        return changed

    def _projection_with_both_credit_notes(
        self,
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Seed distinct line-one and line-two credit-note evidence."""
        changed = self._projection_with_first_credit_note(projection)
        _, second_line = self._line_details(changed)
        second_line[_CREDIT_NOTE_KEY] = deepcopy(_SECOND_CREDIT_NOTE)
        return changed

    def _projection_without_second_credit_note(
        self,
    ) -> list[dict[str, object]]:
        """Contract only line two's optional credit-note member."""
        projection = deepcopy(self.base_projection)
        first_line, second_line = self._line_details(projection)
        if first_line.get(_CREDIT_NOTE_KEY) != _FIRST_CREDIT_NOTE:
            raise AssertionError("line-one credit note must remain exactly 1200 KRW")
        if second_line.get(_CREDIT_NOTE_KEY) != _SECOND_CREDIT_NOTE:
            raise AssertionError("line-two credit note must begin at exactly 800 KRW")
        second_line.pop(_CREDIT_NOTE_KEY)
        return projection

    def _line_segment(
        self,
        payload: bytes,
        line_number: str,
    ) -> tuple[str, int, int]:
        """Return the unique complete LineDtls source block for one line number."""
        text = payload.decode("utf-8")
        marker = f"<Nb>{line_number}</Nb>"
        matches = [
            match
            for match in re.finditer(
                r"<LineDtls>.*?</LineDtls>",
                text,
                re.DOTALL,
            )
            if marker in match.group(0)
        ]
        if len(matches) != 1:
            raise AssertionError(
                f"source must contain one LineDtls for {line_number!r}"
            )
        match = matches[0]
        return match.group(0), match.start(), match.end()

    def _direct_amount_bounds(self, segment: str) -> tuple[int, int]:
        """Reuse the repaired direct-Amount boundary contract from #117."""
        lines = segment.splitlines(keepends=True)
        return self.parent.parent.parent._direct_amount_bounds(lines)

    def _credit_note_source_line(
        self,
        payload: bytes,
        line_number: str,
        amount: str,
    ) -> str:
        """Derive source indentation from the retained direct discount member."""
        segment, _, _ = self._line_segment(payload, line_number)
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self._direct_amount_bounds(segment)
        discount_indexes = [
            index
            for index in range(amount_start + 1, amount_end)
            if lines[index].strip() == "<DscntApldAmt>"
        ]
        if len(discount_indexes) != 1:
            raise AssertionError(
                f"{line_number} must retain exactly one direct discount member"
            )
        source_line = lines[discount_indexes[0]]
        indentation = source_line[: len(source_line) - len(source_line.lstrip(" "))]
        if not indentation or indentation.strip():
            raise AssertionError("direct Amount child indentation must be spaces only")
        return (
            f'{indentation}<CdtNoteAmt Ccy="KRW">{amount}</CdtNoteAmt>\n'
        )

    def _seed_credit_note(
        self,
        payload: bytes,
        line_number: str,
        amount: str,
    ) -> bytes:
        """Insert one credit-note child at its schema-valid direct-Amount position."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self._line_segment(
            payload,
            line_number,
        )
        if "<CdtNoteAmt" in segment:
            raise AssertionError(f"{line_number} must not already contain CdtNoteAmt")
        lines = segment.splitlines(keepends=True)
        amount_start, amount_end = self._direct_amount_bounds(segment)
        source_line = self._credit_note_source_line(
            payload,
            line_number,
            amount,
        )

        # Credit Note Amount follows Discount Applied Amount and precedes later
        # Tax/Adjustment/Remitted children. Insert before the first such sibling,
        # otherwise immediately before the direct Amount closer.
        later_indexes = [
            index
            for index in range(amount_start + 1, amount_end)
            if (
                lines[index].lstrip().startswith("<TaxAmt>")
                or lines[index].lstrip().startswith("<AdjstmntAmtAndRsn>")
                or lines[index].lstrip().startswith("<RmtdAmt ")
            )
        ]
        insert_index = later_indexes[0] if later_indexes else amount_end
        lines.insert(insert_index, source_line)
        seeded_segment = "".join(lines)
        seeded = (
            text[:segment_start] + seeded_segment + text[segment_end:]
        ).encode("utf-8")
        seeded_check, _, _ = self._line_segment(seeded, line_number)
        if seeded_check.count(source_line) != 1:
            raise AssertionError("seeded credit-note source line must occur once")
        return seeded

    def _remove_second_line_credit_note(self, payload: bytes) -> bytes:
        """Remove only line two's exact 800-KRW credit-note source line."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self._line_segment(
            payload,
            "Stockitem2",
        )
        source_line = self._credit_note_source_line(
            payload,
            "Stockitem2",
            "800.00",
        )
        if segment.count(source_line) != 1:
            raise AssertionError(
                "line-two credit-note source line must occur exactly once"
            )
        changed_segment = segment.replace(source_line, "", 1)
        changed = (
            text[:segment_start] + changed_segment + text[segment_end:]
        ).encode("utf-8")
        first_segment, _, _ = self._line_segment(changed, "Stockitem1")
        second_segment, _, _ = self._line_segment(changed, "Stockitem2")
        first_source = self._credit_note_source_line(
            changed,
            "Stockitem1",
            "1200.00",
        )
        if first_segment.count(first_source) != 1:
            raise AssertionError("line-one credit note must remain exact")
        if "<CdtNoteAmt" in second_segment:
            raise AssertionError("line-two credit note must be absent")
        return changed

    def _restore_second_line_credit_note(self, payload: bytes) -> bytes:
        """Restore exactly the omitted line-two credit-note member."""
        second_segment, _, _ = self._line_segment(payload, "Stockitem2")
        if "<CdtNoteAmt" in second_segment:
            raise AssertionError("line-two source must not already contain CdtNoteAmt")
        return self._seed_credit_note(payload, "Stockitem2", "800.00")


if __name__ == "__main__":
    unittest.main()
