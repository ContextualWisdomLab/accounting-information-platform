"""REDs for source-faithful contraction of repeated line-identification evidence."""

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
    test_postgres_bank_statement_structured_referred_document_line_repeated_adjustment_contraction_evidence_red
    as repeated_adjustment_contract,
)

_PARENT_TEST = (
    repeated_adjustment_contract.
    BankStatementStructuredLineRepeatedAdjustmentContractionEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = repeated_adjustment_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = repeated_adjustment_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_ADJUSTMENT_KEY = "adjustments"


class BankStatementStructuredLineRepeatedIdentificationContractionEvidenceRedTests(
    unittest.TestCase
):
    """Preserve a 2-to-1 repeated line-identification contraction."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-adjustment contraction fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only the first repeated Id while retaining canonical SKNB identity."""
        self.parent = _PARENT_TEST(
            "test_adjustment_contraction_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract
        self.line_source_contract = self.parent.line_source_contract

        self.base_payload = self.parent.changed_payload
        self.base_statement = self.parent.changed_statement
        self.base_projection = deepcopy(self.parent.changed_projection)

        first_member, second_member = self._repeated_members(self.base_projection)
        if first_member != {"related_date": "2026-09-02"}:
            raise AssertionError(
                "parent must retain the first repeated Id as Related-Date-only evidence"
            )
        self.canonical_member = {
            "type_code": "SKNB",
            "issuer": str(second_member["issuer"]),
            "number": str(second_member["number"]),
            "related_date": str(second_member["related_date"]),
        }
        if second_member != self.canonical_member:
            raise AssertionError("parent must retain the complete canonical SKNB member")

        self.changed_payload, self.removed_id_block = (
            self._remove_first_repeated_identification(self.base_payload)
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_with_single_identification()
        if self._restore_first_repeated_identification(
            self.changed_payload,
            self.removed_id_block,
        ) != self.base_payload:
            raise AssertionError(
                "exact removed Id bytes must restore the two-member source"
            )

    def test_identification_contraction_is_material_to_each_evidence_hash(self) -> None:
        """Bind the exact 2-to-1 Id contraction to raw and canonical identity."""
        self.assertEqual(
            self._restore_first_repeated_identification(
                self.changed_payload,
                self.removed_id_block,
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

    def test_two_identification_baseline_reads_back_in_source_order(self) -> None:
        """Persist both Id members so relational truncation cannot false-GREEN."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-identification-contraction-two-member-baseline",
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
            expect_repeated=True,
        )
        self.parent._assert_sibling(entries[1], self.base_statement.entries[1])

    def test_rejected_identification_contraction_leaves_no_evidence_residue(
        self,
    ) -> None:
        """Reject Id removal without relational or object-store residue."""
        restricted_owner = self._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-identification-contraction-base",
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
                    "line-identification-contraction-changed",
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

    def test_contracted_identification_reads_back_canonical_single_identity(
        self,
    ) -> None:
        """Persist the changed source without stale repeated-Id population."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-identification-contraction-lookup",
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
            expect_repeated=False,
        )
        self.parent._assert_sibling(entries[1], self.changed_statement.entries[1])

    def _projection_with_single_identification(self) -> list[dict[str, object]]:
        """Contract repeated Ids while retaining the established scalar SKNB view."""
        projection = deepcopy(self.base_projection)
        _, second_line = self.line_source_contract._line_details(projection)
        members = second_line.get("line_identifications")
        if not isinstance(members, list) or len(members) != 2:
            raise AssertionError("base projection must expose exactly two repeated Ids")
        if members[0] != {"related_date": "2026-09-02"}:
            raise AssertionError("first repeated Id must match the exact removable member")
        if members[1] != self.canonical_member:
            raise AssertionError("second repeated Id must remain the canonical SKNB member")
        second_line.pop("line_identifications")
        self._assert_canonical_scalars(second_line)
        return projection

    def _remove_first_repeated_identification(
        self,
        payload: bytes,
    ) -> tuple[bytes, str]:
        """Remove only the first complete Id block and return its exact bytes."""
        text = payload.decode("utf-8")
        segment, start, end = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        blocks = self._identification_blocks(payload)
        if len(blocks) != 2:
            raise AssertionError("base source must contain exactly two repeated Id blocks")
        removed = blocks[0]
        if "<RltdDt>2026-09-02</RltdDt>" not in removed:
            raise AssertionError("first Id must retain the exact Related Date")
        if any(marker in removed for marker in ("<Tp>", "<Issr>", "<Nb>")):
            raise AssertionError("first Id must retain inherited Type/Issuer/Number absences")
        if segment.count(removed) != 1:
            raise AssertionError("first Id block must occur exactly once")
        changed_segment = segment.replace(removed, "", 1)
        changed = (text[:start] + changed_segment + text[end:]).encode("utf-8")
        changed_blocks = self._identification_blocks(changed)
        if len(changed_blocks) != 1:
            raise AssertionError("contraction must retain exactly one Id")
        self._assert_canonical_id_block(changed_blocks[0])
        return changed, removed

    def _restore_first_repeated_identification(
        self,
        payload: bytes,
        removed: str,
    ) -> bytes:
        """Reinsert the exact removed member immediately before canonical SKNB Id."""
        blocks = self._identification_blocks(payload)
        if len(blocks) != 1:
            raise AssertionError("contracted source must contain exactly one Id")
        self._assert_canonical_id_block(blocks[0])
        text = payload.decode("utf-8")
        segment, start, end = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        retained = blocks[0]
        if segment.count(retained) != 1:
            raise AssertionError("retained canonical Id must occur exactly once")
        restored_segment = segment.replace(retained, removed + retained, 1)
        return (text[:start] + restored_segment + text[end:]).encode("utf-8")

    def _identification_blocks(self, payload: bytes) -> list[str]:
        """Return complete direct Id blocks from the exact Stockitem2 line."""
        segment, _, _ = self.line_source_contract._line_segment(
            payload,
            "Stockitem2",
        )
        lines = segment.splitlines(keepends=True)
        blocks: list[str] = []
        index = 0
        while index < len(lines):
            if lines[index].strip() != "<Id>":
                index += 1
                continue
            outer_indent = self._indent(lines[index])
            close_index = index + 1
            while close_index < len(lines):
                if (
                    lines[close_index].strip() == "</Id>"
                    and self._indent(lines[close_index]) == outer_indent
                ):
                    break
                close_index += 1
            if close_index >= len(lines):
                raise AssertionError("direct Id block has no matching close")
            blocks.append("".join(lines[index : close_index + 1]))
            index = close_index + 1
        return blocks

    def _assert_canonical_id_block(self, block: str) -> None:
        """Pin the one retained Id to exact SKNB issuer/number/date evidence."""
        markers = (
            "<Cd>SKNB</Cd>",
            f'<Issr>{self.canonical_member["issuer"]}</Issr>',
            f'<Nb>{self.canonical_member["number"]}</Nb>',
            f'<RltdDt>{self.canonical_member["related_date"]}</RltdDt>',
        )
        if not all(marker in block for marker in markers):
            raise AssertionError("retained Id must be the complete canonical SKNB member")

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        *,
        expect_repeated: bool,
    ) -> None:
        """Assert exact persisted identity, Id cardinality, and transaction truth."""
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
        first_line, second_line = self.line_source_contract._line_details(
            persisted_detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.parent.first_line_projection)
        self.assertEqual(
            second_line.get(_ADJUSTMENT_KEY),
            [deepcopy(repeated_adjustment_contract._ADJT)],
        )
        self._assert_canonical_scalars(second_line)
        if expect_repeated:
            self.assertEqual(
                second_line.get("line_identifications"),
                [{"related_date": "2026-09-02"}, self.canonical_member],
            )
        else:
            self.assertNotIn("line_identifications", second_line)

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

    def _assert_canonical_scalars(self, second_line: dict[str, object]) -> None:
        """Keep the established single-value compatibility view on SKNB."""
        self.assertEqual(second_line.get("line_type_code"), "SKNB")
        self.assertEqual(
            second_line.get("line_type_issuer"),
            self.canonical_member["issuer"],
        )
        self.assertEqual(
            second_line.get("line_number"),
            self.canonical_member["number"],
        )
        self.assertEqual(
            second_line.get("related_date"),
            self.canonical_member["related_date"],
        )

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

    @staticmethod
    def _repeated_members(
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return both Id mappings from the exact second source line."""
        if len(projection) != 1:
            raise AssertionError("contraction RED requires one referred document")
        line_details = projection[0].get("line_details")
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("contraction RED requires two source lines")
        second_line = line_details[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second line projection must be a mapping")
        members = second_line.get("line_identifications")
        if not isinstance(members, list) or len(members) != 2:
            raise AssertionError("contraction RED requires two repeated Id members")
        if not all(isinstance(member, dict) for member in members):
            raise AssertionError("every repeated Id projection must be a mapping")
        return members[0], members[1]

    @staticmethod
    def _indent(line: str) -> str:
        """Return leading spaces from one source line."""
        return line[: len(line) - len(line.lstrip(" "))]


if __name__ == "__main__":
    unittest.main()
