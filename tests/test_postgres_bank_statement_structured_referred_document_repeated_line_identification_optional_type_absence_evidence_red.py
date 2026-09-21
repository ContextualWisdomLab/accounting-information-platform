"""REDs for optional Type absence inside repeated referred-document line Identifications."""

from __future__ import annotations

import hashlib
import unittest
from copy import deepcopy
from decimal import Decimal
from typing import Any

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
    test_postgres_bank_statement_structured_referred_document_repeated_line_identification_optional_related_date_absence_evidence_red
    as optional_related_date_contract,
)

_PARENT_TEST = (
    optional_related_date_contract.BankStatementStructuredRepeatedLineIdentificationOptionalRelatedDateAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = optional_related_date_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = optional_related_date_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredRepeatedLineIdentificationOptionalTypeAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve an absent optional Type on the exact repeated Id member."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Related-Date-absence fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove member one's Type while keeping its Related Date and sibling identity."""
        self.parent = _PARENT_TEST(
            "test_repeated_identification_optional_related_date_absence_is_material_to_each_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # Use #113's base rather than its changed payload so the target member
        # retains Related Date; Type absence is therefore not an empty-Id edge case.
        self.base_payload = self.parent.base_payload
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.base_projection)
        first_member, second_member = self._repeated_members(self.base_projection)
        self.base_type = str(first_member["type_proprietary"])
        self.base_related_date = str(first_member["related_date"])
        self.sibling_number = str(second_member["number"])
        self.sibling_issuer = str(second_member["issuer"])
        self.sibling_related_date = str(second_member["related_date"])
        self.base_type_block = self._extract_first_repeated_type_block(self.base_payload)

        self.changed_payload = self._remove_first_repeated_type(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_first_repeated_type()

    def test_repeated_identification_optional_type_absence_is_material_to_each_hash(
        self,
    ) -> None:
        """Bind one member-local Type omission to raw through statement identity."""
        self.assertEqual(
            self._restore_first_repeated_type(self.changed_payload),
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

    def test_rejected_optional_type_absence_change_preserves_all_evidence(
        self,
    ) -> None:
        """Reject Type omission without relational or raw-artifact residue."""
        restricted_owner = self._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "repeated-line-identification-optional-type-base",
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
                    "repeated-line-identification-optional-type-absent",
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

    def test_buyer_read_preserves_absent_type_on_exact_repeated_member(
        self,
    ) -> None:
        """Expose no Type on member one without copying member two's coded Type."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "repeated-line-identification-optional-type-lookup",
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
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]
        changed_entry = self.changed_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]

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

        second_line = detail[_STRUCTURED_EVIDENCE_KEY][0]["line_details"][1]
        identifications = second_line["line_identifications"]
        self.assertEqual(len(identifications), 2)
        self.assertNotIn("type_code", identifications[0])
        self.assertNotIn("type_proprietary", identifications[0])
        self.assertNotIn("issuer", identifications[0])
        self.assertNotIn("number", identifications[0])
        self.assertEqual(
            identifications[0],
            {"related_date": self.base_related_date},
        )
        self.assertEqual(
            identifications[1],
            {
                "type_code": "SKNB",
                "issuer": self.sibling_issuer,
                "number": self.sibling_number,
                "related_date": self.sibling_related_date,
            },
        )
        self.assertEqual(second_line["line_type_code"], "SKNB")
        self.assertEqual(second_line["line_number"], self.sibling_number)
        self.assertEqual(second_line["line_type_issuer"], self.sibling_issuer)
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_first_repeated_type(
        self,
    ) -> list[dict[str, object]]:
        """Remove Type from the first repeated Id while retaining its Related Date."""
        projection = deepcopy(self.base_projection)
        first_member, second_member = self._repeated_members(projection)
        if "issuer" in first_member or "number" in first_member:
            raise AssertionError(
                "first repeated Id must retain inherited Issuer and Number absence"
            )
        if first_member.get("type_proprietary") != self.base_type:
            raise AssertionError("first repeated Id must begin with its exact proprietary Type")
        if first_member.get("related_date") != self.base_related_date:
            raise AssertionError("first repeated Id must retain its exact Related Date")
        if second_member.get("type_code") != "SKNB":
            raise AssertionError("second repeated Id must retain coded SKNB Type")
        first_member.pop("type_proprietary")
        return projection

    def _extract_first_repeated_type_block(self, payload: bytes) -> str:
        """Return the exact complete Type block on the first repeated Id."""
        text = payload.decode("utf-8")
        line_segment, _, _ = self._second_line_segment(text)
        _, _, block = self._target_first_repeated_id(line_segment, require_type=True)
        start = block.find("<Tp>")
        end_tag = "</Tp>"
        end = block.find(end_tag, start)
        if start < 0 or end < 0:
            raise AssertionError("first repeated Id requires a complete Type block")
        line_start = block.rfind("\n", 0, start) + 1
        line_end = block.find("\n", end + len(end_tag))
        if line_end < 0:
            raise AssertionError("first repeated Id Type block must end before another line")
        type_block = block[line_start : line_end + 1]
        if type_block.count("<Tp>") != 1 or type_block.count("</Tp>") != 1:
            raise AssertionError("first repeated Id requires one exact Type block")
        if f"<Prtry>{self.base_type}</Prtry>" not in type_block:
            raise AssertionError("first repeated Id Type block must retain proprietary value")
        return type_block

    def _remove_first_repeated_type(self, payload: bytes) -> bytes:
        """Remove exact first-member Type while retaining member-local Related Date."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self._second_line_segment(text)
        block_start, block_end, block = self._target_first_repeated_id(
            line_segment,
            require_type=True,
        )
        if block.count(self.base_type_block) != 1:
            raise AssertionError("first repeated Id must contain its exact Type block once")
        related_date_marker = f"<RltdDt>{self.base_related_date}</RltdDt>"
        if related_date_marker not in block:
            raise AssertionError("first repeated Id must retain Related Date before Type removal")
        changed_block = block.replace(self.base_type_block, "", 1)
        changed_line = line_segment[:block_start] + changed_block + line_segment[block_end:]
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _restore_first_repeated_type(self, payload: bytes) -> bytes:
        """Restore the exact Type block before the surviving Related Date."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self._second_line_segment(text)
        block_start, block_end, block = self._target_first_repeated_id(
            line_segment,
            require_type=False,
        )
        if "<Tp>" in block or "</Tp>" in block:
            raise AssertionError("type-absence fixture must omit Type on target member")
        related_date_marker = f"<RltdDt>{self.base_related_date}</RltdDt>"
        related_pos = block.find(related_date_marker)
        if related_pos < 0:
            raise AssertionError("target member must retain its Related Date")
        related_line_start = block.rfind("\n", 0, related_pos) + 1
        restored_block = (
            block[:related_line_start]
            + self.base_type_block
            + block[related_line_start:]
        )
        restored_line = line_segment[:block_start] + restored_block + line_segment[block_end:]
        return (text[:line_start] + restored_line + text[line_end:]).encode("utf-8")

    def _target_first_repeated_id(
        self,
        line_segment: str,
        *,
        require_type: bool,
    ) -> tuple[int, int, str]:
        """Locate the first repeated Id and verify exact sibling/member boundaries."""
        starts: list[int] = []
        cursor = 0
        while True:
            start = line_segment.find("<Id>", cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len("<Id>")
        if len(starts) != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("optional-Type RED requires exactly two repeated Id blocks")

        blocks: list[tuple[int, int, str]] = []
        for start in starts:
            end_start = line_segment.find("</Id>", start)
            if end_start < 0:
                raise AssertionError("every repeated Id requires a closing tag")
            end = end_start + len("</Id>")
            blocks.append((start, end, line_segment[start:end]))

        first = blocks[0]
        second = blocks[1]
        if "<Issr>" in first[2] or "<Nb>" in first[2]:
            raise AssertionError("first repeated Id must retain inherited optional absences")
        if f"<RltdDt>{self.base_related_date}</RltdDt>" not in first[2]:
            raise AssertionError("first repeated Id must retain its Related Date")
        has_type = f"<Prtry>{self.base_type}</Prtry>" in first[2]
        if require_type and not has_type:
            raise AssertionError("first repeated Id must retain proprietary Type before omission")
        if not require_type and (
            "<Tp>" in first[2]
            or "</Tp>" in first[2]
            or "<Cd>" in first[2]
            or "<Prtry>" in first[2]
        ):
            raise AssertionError("first repeated Id must contain no Type representation")
        if "<Cd>SKNB</Cd>" not in second[2]:
            raise AssertionError("second repeated Id must retain SKNB Type")
        if f"<Issr>{self.sibling_issuer}</Issr>" not in second[2]:
            raise AssertionError("second repeated Id must retain its Issuer")
        if f"<Nb>{self.sibling_number}</Nb>" not in second[2]:
            raise AssertionError("second repeated Id must retain its Number")
        if f"<RltdDt>{self.sibling_related_date}</RltdDt>" not in second[2]:
            raise AssertionError("second repeated Id must retain its Related Date")
        return first

    def _second_line_segment(self, text: str) -> tuple[str, int, int]:
        """Delegate exact second-line scoping to the nearest inherited owner."""
        owner = self._ancestor_with("_second_line_segment")
        return owner._second_line_segment(text)

    def _ancestor_with(self, *method_names: str) -> Any:
        """Find the nearest inherited fixture owner exposing all requested helpers."""
        owner: Any = self.parent
        for _ in range(32):
            if all(hasattr(owner, name) for name in method_names):
                return owner
            if not hasattr(owner, "parent"):
                break
            owner = owner.parent
        raise AssertionError(
            "inherited fixture chain does not expose required helpers: "
            + ", ".join(method_names)
        )

    @staticmethod
    def _repeated_members(
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return the two repeated Id mappings from the exact second source line."""
        if len(projection) != 1:
            raise AssertionError("optional-Type RED requires one referred document")
        line_details = projection[0].get("line_details")
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("optional-Type RED requires two source lines")
        second_line = line_details[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second line projection must be a mapping")
        members = second_line.get("line_identifications")
        if not isinstance(members, list) or len(members) != 2:
            raise AssertionError("optional-Type RED requires two repeated Id members")
        if not all(isinstance(member, dict) for member in members):
            raise AssertionError("every repeated Id projection must be a mapping")
        return members[0], members[1]


if __name__ == "__main__":
    unittest.main()
