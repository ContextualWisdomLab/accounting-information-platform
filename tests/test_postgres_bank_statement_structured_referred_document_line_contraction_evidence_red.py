"""REDs for source-faithful contraction of referred-document line details."""

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
    test_postgres_bank_statement_structured_referred_document_line_repeated_identification_contraction_evidence_red
    as repeated_identification_contract,
)

_PARENT_TEST = (
    repeated_identification_contract.
    BankStatementStructuredLineRepeatedIdentificationContractionEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = repeated_identification_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = repeated_identification_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineContractionEvidenceRedTests(unittest.TestCase):
    """Preserve a two-to-one referred-document line contraction."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-identification contraction fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only Stockitem1 while retaining the complete Stockitem2 line."""
        self.parent = _PARENT_TEST(
            "test_identification_contraction_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract
        self.line_source_contract = self.parent.line_source_contract

        self.base_payload = self.parent.changed_payload
        self.base_statement = self.parent.changed_statement
        self.base_projection = deepcopy(self.parent.changed_projection)
        base_lines = self._line_details(self.base_projection)
        if len(base_lines) != 2:
            raise AssertionError("parent must retain exactly two referred-document lines")
        if base_lines[0].get("line_number") != "Stockitem1":
            raise AssertionError("first source line must remain Stockitem1")
        if base_lines[1].get("line_number") != "Stockitem2":
            raise AssertionError("second source line must remain Stockitem2")
        self.removed_line_projection = deepcopy(base_lines[0])
        self.retained_line_projection = deepcopy(base_lines[1])
        if "line_identifications" in self.retained_line_projection:
            raise AssertionError(
                "parent contraction must already expose canonical scalar-only Stockitem2"
            )

        (
            self.changed_payload,
            self.removed_line_block,
            self.removed_line_offset,
        ) = self._remove_first_line_detail(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_with_single_line()
        if self._restore_first_line_detail(
            self.changed_payload,
            self.removed_line_block,
            self.removed_line_offset,
        ) != self.base_payload:
            raise AssertionError(
                "exact removed LineDtls bytes must restore the two-line source"
            )

    def test_line_contraction_is_material_to_each_evidence_hash(self) -> None:
        """Bind exact two-to-one LineDtls contraction to raw and canonical identity."""
        self.assertEqual(
            self._restore_first_line_detail(
                self.changed_payload,
                self.removed_line_block,
                self.removed_line_offset,
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

    def test_two_line_baseline_reads_back_in_source_order(self) -> None:
        """Persist both lines so relational line truncation cannot false-GREEN."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-detail-contraction-two-line-baseline",
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
            expected_line_numbers=("Stockitem1", "Stockitem2"),
        )
        self.parent._assert_sibling(entries[1], self.base_statement.entries[1])

    def test_rejected_line_contraction_leaves_no_evidence_residue(self) -> None:
        """Reject LineDtls removal without relational or object-store residue."""
        restricted_owner = self._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-detail-contraction-base",
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
        self.assertTrue(before_rows["bank_statement_artifact"])
        self.assertTrue(before_rows["bank_statement_entry"])
        self.assertTrue(before_rows["bank_statement_entry_detail"])
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
                    "line-detail-contraction-changed",
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

    def test_contracted_line_reads_back_only_retained_source_line(self) -> None:
        """Persist the one-line source without stale Stockitem1 evidence."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-detail-contraction-lookup",
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
            expected_line_numbers=("Stockitem2",),
        )
        self.parent._assert_sibling(entries[1], self.changed_statement.entries[1])

    def _projection_with_single_line(self) -> list[dict[str, object]]:
        """Remove only Stockitem1 from the expected ordered line projection."""
        projection = deepcopy(self.base_projection)
        lines = self._line_details(projection)
        if len(lines) != 2:
            raise AssertionError("base projection must contain exactly two lines")
        removed = lines.pop(0)
        if removed != self.removed_line_projection:
            raise AssertionError("projection contraction must remove exact Stockitem1")
        if lines != [self.retained_line_projection]:
            raise AssertionError("projection contraction must retain exact Stockitem2")
        return projection

    def _remove_first_line_detail(
        self,
        payload: bytes,
    ) -> tuple[bytes, str, int]:
        """Remove complete Stockitem1 LineDtls bytes and retain their exact offset."""
        text = payload.decode("utf-8")
        first_segment, first_start, first_end = self.line_source_contract._line_segment(
            payload,
            "Stockitem1",
        )
        second_segment, second_start, _ = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        if first_start >= second_start:
            raise AssertionError("Stockitem1 must precede Stockitem2 in source order")
        if first_segment.count("<LineDtls>") != 1 or first_segment.count("</LineDtls>") != 1:
            raise AssertionError("target segment must be one complete LineDtls block")
        if "<Nb>Stockitem1</Nb>" not in first_segment:
            raise AssertionError("target line block must be Stockitem1")
        if "Stockitem2" in first_segment:
            raise AssertionError("target block must not contain the retained source line")
        if text.count(first_segment) != 1:
            raise AssertionError("target LineDtls block must occur exactly once")
        if "<Nb>Stockitem2</Nb>" not in second_segment:
            raise AssertionError("retained block must remain Stockitem2")
        if text[first_start:first_end] != first_segment:
            raise AssertionError("line helper boundaries must equal exact removed bytes")
        changed = (text[:first_start] + text[first_end:]).encode("utf-8")
        changed_lines = self._source_line_numbers(changed)
        if changed_lines != ["Stockitem2"]:
            raise AssertionError("contraction must retain only Stockitem2")
        return changed, first_segment, first_start

    def _restore_first_line_detail(
        self,
        payload: bytes,
        removed: str,
        offset: int,
    ) -> bytes:
        """Reinsert exact Stockitem1 bytes at their original source offset."""
        text = payload.decode("utf-8")
        if not 0 <= offset <= len(text):
            raise AssertionError("removed LineDtls offset must remain within source bounds")
        restored = (text[:offset] + removed + text[offset:]).encode("utf-8")
        if self._source_line_numbers(restored) != ["Stockitem1", "Stockitem2"]:
            raise AssertionError("restoration must recover original line source order")
        return restored

    @staticmethod
    def _source_line_numbers(payload: bytes) -> list[str]:
        """Return exact source line identifiers still present after contraction."""
        text = payload.decode("utf-8")
        return [
            number
            for number in ("Stockitem1", "Stockitem2")
            if f"<Nb>{number}</Nb>" in text
        ]

    @staticmethod
    def _line_details(projection: list[dict[str, object]]) -> list[dict[str, object]]:
        """Return the sole referred document's ordered line-detail population."""
        if len(projection) != 1:
            raise AssertionError("line contraction RED requires one referred document")
        lines = projection[0].get("line_details")
        if not isinstance(lines, list):
            raise AssertionError("referred document must expose line_details")
        if not all(isinstance(line, dict) for line in lines):
            raise AssertionError("every line detail projection must be a mapping")
        return lines

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        *,
        expected_line_numbers: tuple[str, ...],
    ) -> None:
        """Assert persisted line cardinality, hashes, projection, and transaction truth."""
        persisted_details = persisted_entry["entry_details"]
        self.assertEqual(
            len(persisted_details),
            len(expected_entry.entry_details),
        )
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
        persisted_lines = self._line_details(
            persisted_detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(
            tuple(str(line.get("line_number")) for line in persisted_lines),
            expected_line_numbers,
        )
        if expected_line_numbers == ("Stockitem2",):
            self.assertEqual(persisted_lines[0], self.retained_line_projection)
            self.assertNotEqual(persisted_lines[0], self.removed_line_projection)
        elif expected_line_numbers == ("Stockitem1", "Stockitem2"):
            self.assertEqual(
                persisted_lines,
                [self.removed_line_projection, self.retained_line_projection],
            )
        else:
            raise AssertionError("unexpected line contraction oracle")

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

    def _ancestor_with(self, *names: str) -> object:
        """Find the nearest inherited test owner exposing all requested helpers."""
        current: object | None = self.parent
        while current is not None:
            if all(hasattr(current, name) for name in names):
                return current
            current = getattr(current, "parent", None)
        raise AssertionError(f"no inherited owner exposes helpers {names!r}")


if __name__ == "__main__":
    unittest.main()
