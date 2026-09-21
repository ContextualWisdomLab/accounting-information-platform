"""REDs for optional Issuer absence inside repeated referred-document line Identifications."""

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
    test_postgres_bank_statement_structured_referred_document_repeated_line_identification_related_date_evidence_red
    as related_date_contract,
)

_PARENT_TEST = (
    related_date_contract.BankStatementStructuredRepeatedLineIdentificationRelatedDateEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = related_date_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = related_date_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredRepeatedLineIdentificationOptionalIssuerAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve an absent optional Issuer on the exact repeated Id member."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-Id Related-Date fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only the first repeated member's Issuer while keeping its identity stable."""
        self.parent = _PARENT_TEST(
            "test_repeated_identification_related_date_is_material_to_each_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_issuer = self.parent.parent.parent.parent.prnb_issuer
        self.base_payload = self.parent.base_payload
        self.changed_payload = self._remove_first_repeated_issuer(self.base_payload)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.base_projection)
        self.changed_projection = self._projection_without_first_repeated_issuer()

    def test_repeated_identification_optional_issuer_absence_is_material_to_each_hash(
        self,
    ) -> None:
        """Bind one member-local Issuer omission to raw, detail, entry, and statement identity."""
        self.assertEqual(
            self._restore_first_repeated_issuer(self.changed_payload),
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

    def test_rejected_optional_issuer_absence_change_preserves_all_evidence(self) -> None:
        """Reject an Issuer-omission replay without relational or raw-artifact residue."""
        restricted_owner = self.parent.parent.parent.parent.parent.parent
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "repeated-line-identification-optional-issuer-base",
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
                    "repeated-line-identification-optional-issuer-absent",
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

    def test_buyer_read_preserves_absent_issuer_on_exact_repeated_member(self) -> None:
        """Expose no Issuer on member one without copying member two's Issuer across."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "repeated-line-identification-optional-issuer-lookup",
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
        self.assertEqual(
            identifications[0],
            {
                "type_proprietary": self.parent.parent.parent.base_proprietary_type,
                "number": self.parent.parent.base_number,
                "related_date": self.parent.base_related_date,
            },
        )
        self.assertEqual(
            identifications[1],
            {
                "type_code": "SKNB",
                "issuer": self.parent.parent.parent.parent.sknb_issuer,
                "number": self.line_contract.second_line_number,
                "related_date": self.line_contract.line_related_date,
            },
        )
        self.assertEqual(
            second_line["line_type_issuer"],
            self.parent.parent.parent.parent.sknb_issuer,
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_first_repeated_issuer(self) -> list[dict[str, object]]:
        """Remove only the optional Issuer from the proprietary first repeated Id."""
        projection = deepcopy(self.parent.base_projection)
        first_member, second_member = self.parent.parent._repeated_members(projection)
        if first_member.get("issuer") != self.base_issuer:
            raise AssertionError("first repeated Id must begin with PARTS-SCHEME issuer")
        if second_member.get("issuer") != self.parent.parent.parent.parent.sknb_issuer:
            raise AssertionError("second repeated Id must retain STOCK-SCHEME issuer")
        first_member.pop("issuer")
        return projection

    def _remove_first_repeated_issuer(self, payload: bytes) -> bytes:
        """Remove the exact first repeated Id Issuer and leave the sibling Issuer intact."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = (
            self.parent.parent.parent.parent.parent.parent._second_line_segment(text)
        )
        block_start, block_end, block = self._target_first_repeated_id(line_segment)
        marker = f"                      <Issr>{self.base_issuer}</Issr>\n"
        if block.count(marker) != 1:
            raise AssertionError("first repeated Id must contain its exact optional Issuer once")
        sibling_issuer = self.parent.parent.parent.parent.sknb_issuer
        if f"<Issr>{sibling_issuer}</Issr>" in block:
            raise AssertionError("first repeated Id must not carry sibling Issuer")
        changed_block = block.replace(marker, "", 1)
        changed_line = line_segment[:block_start] + changed_block + line_segment[block_end:]
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _restore_first_repeated_issuer(self, payload: bytes) -> bytes:
        """Restore the omitted first-member Issuer at the schema-valid Type/Number boundary."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = (
            self.parent.parent.parent.parent.parent.parent._second_line_segment(text)
        )
        block_start, block_end, block = self._target_first_repeated_id(line_segment)
        if "<Issr>" in block or "</Issr>" in block:
            raise AssertionError("issuer-absence fixture must omit Issuer on target member")
        boundary = (
            "                      </Tp>\n"
            "                      <Nb>"
        )
        if block.count(boundary) != 1:
            raise AssertionError("target Type/Number boundary must be unique")
        restored_block = block.replace(
            boundary,
            (
                "                      </Tp>\n"
                f"                      <Issr>{self.base_issuer}</Issr>\n"
                "                      <Nb>"
            ),
            1,
        )
        restored_line = line_segment[:block_start] + restored_block + line_segment[block_end:]
        return (text[:line_start] + restored_line + text[line_end:]).encode("utf-8")

    def _target_first_repeated_id(self, line_segment: str) -> tuple[int, int, str]:
        """Locate the proprietary first repeated Id by Type, Number, and Related Date."""
        starts: list[int] = []
        cursor = 0
        while True:
            start = line_segment.find("<Id>", cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len("<Id>")
        if len(starts) != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("optional-issuer RED requires exactly two repeated Id blocks")

        target_type = self.parent.parent.parent.base_proprietary_type
        target_number = self.parent.parent.base_number
        target_date = self.parent.base_related_date
        matches: list[tuple[int, int, str]] = []
        for start in starts:
            end_start = line_segment.find("</Id>", start)
            if end_start < 0:
                raise AssertionError("every repeated Id requires a closing tag")
            end = end_start + len("</Id>")
            block = line_segment[start:end]
            if (
                f"<Prtry>{target_type}</Prtry>" in block
                and f"<Nb>{target_number}</Nb>" in block
                and f"<RltdDt>{target_date}</RltdDt>" in block
            ):
                matches.append((start, end, block))
        if len(matches) != 1:
            raise AssertionError("proprietary first repeated Id must be unique")
        if matches[0][0] != starts[0]:
            raise AssertionError("target repeated Id must remain the first source member")
        return matches[0]


if __name__ == "__main__":
    unittest.main()
