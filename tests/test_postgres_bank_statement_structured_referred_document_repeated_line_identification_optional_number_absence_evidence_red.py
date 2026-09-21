"""REDs for optional Number absence inside repeated referred-document line Identifications."""

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
    test_postgres_bank_statement_structured_referred_document_repeated_line_identification_optional_issuer_absence_evidence_red
    as optional_issuer_contract,
)

_PARENT_TEST = (
    optional_issuer_contract.BankStatementStructuredRepeatedLineIdentificationOptionalIssuerAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = optional_issuer_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = optional_issuer_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredRepeatedLineIdentificationOptionalNumberAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve an absent optional Number on the exact repeated Id member."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Issuer-absence fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only member one's Number after inheriting its absent Issuer."""
        self.parent = _PARENT_TEST(
            "test_repeated_identification_optional_issuer_absence_is_material_to_each_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_payload = self.parent.changed_payload
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.changed_projection)
        first_member, second_member = self._repeated_members(self.base_projection)
        self.base_type = str(first_member["type_proprietary"])
        self.base_number = str(first_member["number"])
        self.base_related_date = str(first_member["related_date"])
        self.sibling_number = str(second_member["number"])
        self.sibling_issuer = str(second_member["issuer"])

        self.changed_payload = self._remove_first_repeated_number(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_first_repeated_number()

    def test_repeated_identification_optional_number_absence_is_material_to_each_hash(
        self,
    ) -> None:
        """Bind one member-local Number omission to raw, detail, entry, and statement identity."""
        self.assertEqual(
            self._restore_first_repeated_number(self.changed_payload),
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

    def test_rejected_optional_number_absence_change_preserves_all_evidence(self) -> None:
        """Reject a Number-omission replay without relational or raw-artifact residue."""
        restricted_owner = self.parent.parent.parent.parent.parent.parent.parent
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "repeated-line-identification-optional-number-base",
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
                    "repeated-line-identification-optional-number-absent",
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

    def test_buyer_read_preserves_absent_number_on_exact_repeated_member(self) -> None:
        """Expose no Number on member one without copying member two's Number across."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "repeated-line-identification-optional-number-lookup",
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
        self.assertNotIn("issuer", identifications[0])
        self.assertNotIn("number", identifications[0])
        self.assertEqual(
            identifications[0],
            {
                "type_proprietary": self.base_type,
                "related_date": self.base_related_date,
            },
        )
        self.assertEqual(
            identifications[1],
            {
                "type_code": "SKNB",
                "issuer": self.sibling_issuer,
                "number": self.sibling_number,
                "related_date": self.line_contract.line_related_date,
            },
        )
        self.assertEqual(second_line["line_number"], self.sibling_number)
        self.assertEqual(second_line["line_type_issuer"], self.sibling_issuer)
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_first_repeated_number(self) -> list[dict[str, object]]:
        """Remove only Number from the already issuer-absent first repeated Id."""
        projection = deepcopy(self.base_projection)
        first_member, second_member = self._repeated_members(projection)
        if "issuer" in first_member:
            raise AssertionError("first repeated Id must retain inherited Issuer absence")
        if first_member.get("number") != self.base_number:
            raise AssertionError("first repeated Id must begin with its exact Number")
        if second_member.get("number") != self.sibling_number:
            raise AssertionError("second repeated Id must retain Stockitem2 Number")
        first_member.pop("number")
        return projection

    def _remove_first_repeated_number(self, payload: bytes) -> bytes:
        """Remove the exact first repeated Id Number and leave sibling identity intact."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = (
            self.parent.parent.parent.parent.parent.parent.parent._second_line_segment(text)
        )
        block_start, block_end, block = self._target_first_repeated_id(line_segment)
        marker = f"                      <Nb>{self.base_number}</Nb>\n"
        if block.count(marker) != 1:
            raise AssertionError("first repeated Id must contain its exact Number once")
        if f"<Nb>{self.sibling_number}</Nb>" in block:
            raise AssertionError("first repeated Id must not carry sibling Number")
        if "<Issr>" in block or "</Issr>" in block:
            raise AssertionError("first repeated Id must retain inherited Issuer absence")
        changed_block = block.replace(marker, "", 1)
        changed_line = line_segment[:block_start] + changed_block + line_segment[block_end:]
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _restore_first_repeated_number(self, payload: bytes) -> bytes:
        """Restore the omitted Number at the schema-valid Type/Related-Date boundary."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = (
            self.parent.parent.parent.parent.parent.parent.parent._second_line_segment(text)
        )
        block_start, block_end, block = self._target_first_repeated_id(line_segment)
        if "<Nb>" in block or "</Nb>" in block:
            raise AssertionError("number-absence fixture must omit Number on target member")
        if "<Issr>" in block or "</Issr>" in block:
            raise AssertionError("number-absence fixture must retain inherited Issuer absence")
        boundary = (
            "                      </Tp>\n"
            "                      <RltdDt>"
        )
        if block.count(boundary) != 1:
            raise AssertionError("target Type/Related-Date boundary must be unique")
        restored_block = block.replace(
            boundary,
            (
                "                      </Tp>\n"
                f"                      <Nb>{self.base_number}</Nb>\n"
                "                      <RltdDt>"
            ),
            1,
        )
        restored_line = line_segment[:block_start] + restored_block + line_segment[block_end:]
        return (text[:line_start] + restored_line + text[line_end:]).encode("utf-8")

    def _target_first_repeated_id(self, line_segment: str) -> tuple[int, int, str]:
        """Locate first repeated Id by source ordinal, proprietary Type, and Related Date."""
        starts: list[int] = []
        cursor = 0
        while True:
            start = line_segment.find("<Id>", cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len("<Id>")
        if len(starts) != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("optional-number RED requires exactly two repeated Id blocks")

        blocks: list[tuple[int, int, str]] = []
        for start in starts:
            end_start = line_segment.find("</Id>", start)
            if end_start < 0:
                raise AssertionError("every repeated Id requires a closing tag")
            end = end_start + len("</Id>")
            blocks.append((start, end, line_segment[start:end]))

        first = blocks[0]
        second = blocks[1]
        if f"<Prtry>{self.base_type}</Prtry>" not in first[2]:
            raise AssertionError("first repeated Id must retain proprietary Type")
        if f"<RltdDt>{self.base_related_date}</RltdDt>" not in first[2]:
            raise AssertionError("first repeated Id must retain its Related Date")
        if "<Cd>SKNB</Cd>" not in second[2]:
            raise AssertionError("second repeated Id must retain SKNB Type")
        if f"<Nb>{self.sibling_number}</Nb>" not in second[2]:
            raise AssertionError("second repeated Id must retain its Number")
        if f"<Issr>{self.sibling_issuer}</Issr>" not in second[2]:
            raise AssertionError("second repeated Id must retain its Issuer")
        return first

    @staticmethod
    def _repeated_members(
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return the two repeated Id mappings from the exact second source line."""
        if len(projection) != 1:
            raise AssertionError("optional-number RED requires one referred document")
        line_details = projection[0].get("line_details")
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("optional-number RED requires two source lines")
        second_line = line_details[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second line projection must be a mapping")
        members = second_line.get("line_identifications")
        if not isinstance(members, list) or len(members) != 2:
            raise AssertionError("optional-number RED requires two repeated Id members")
        if not all(isinstance(member, dict) for member in members):
            raise AssertionError("every repeated Id projection must be a mapping")
        return members[0], members[1]


if __name__ == "__main__":
    unittest.main()
