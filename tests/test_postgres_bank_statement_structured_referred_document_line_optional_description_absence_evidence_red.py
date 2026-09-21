"""REDs for optional referred-document line Description absence evidence."""

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
    test_postgres_bank_statement_structured_referred_document_repeated_line_identification_optional_type_absence_evidence_red
    as optional_type_contract,
)

_PARENT_TEST = (
    optional_type_contract.BankStatementStructuredRepeatedLineIdentificationOptionalTypeAbsenceEvidenceRedTests
)
_NORMALIZATION_PARENT_TEST = optional_type_contract._NORMALIZATION_PARENT_TEST
_CORRECTION_ERROR = optional_type_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineOptionalDescriptionAbsenceEvidenceRedTests(
    unittest.TestCase
):
    """Preserve Description absence on the exact referred-document source line."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL optional-Type-absence fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only line two Description while retaining the parent Type omission."""
        self.parent = _PARENT_TEST(
            "test_repeated_identification_optional_type_absence_is_material_to_each_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        # Compose on #114's changed source so the parent's optional-Type-absence
        # finding remains present. This child changes only LineDtls/Desc.
        self.base_payload = self.parent.changed_payload
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.changed_projection)
        first_line, second_line = self._line_details(self.base_projection)
        self.first_line_description = str(first_line["description"])
        self.second_line_description = str(second_line["description"])
        if self.first_line_description == self.second_line_description:
            raise AssertionError("description-absence RED requires distinct sibling descriptions")
        self.description_line = self._extract_second_line_description_line(self.base_payload)

        self.changed_payload = self._remove_second_line_description(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._projection_without_second_line_description()

    def test_optional_description_absence_is_material_to_each_evidence_hash(self) -> None:
        """Bind one source-line Description omission from raw bytes through statement identity."""
        self.assertEqual(
            self._restore_second_line_description(self.changed_payload),
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

    def test_rejected_description_absence_change_leaves_no_evidence_residue(self) -> None:
        """Reject Description omission without changing relational or raw-source evidence."""
        restricted_owner = self.parent._ancestor_with(
            "_restricted_bank_statement_runtime_url",
            "_tenant_statement_rows",
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-optional-description-base",
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
                    "line-optional-description-absent",
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

    def test_buyer_read_keeps_description_absent_on_exact_source_line(self) -> None:
        """Expose no second-line Description without copying the first line's text."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-optional-description-lookup",
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

        first_line, second_line = self._line_details(detail[_STRUCTURED_EVIDENCE_KEY])
        self.assertEqual(first_line["description"], self.first_line_description)
        self.assertNotIn("description", second_line)

        # The direct parent contract remains live in this descendant: first repeated
        # Identification omits Type while retaining Related Date, and the sibling
        # remains the canonical SKNB member rather than filling the omission.
        identifications = second_line["line_identifications"]
        self.assertEqual(len(identifications), 2)
        self.assertEqual(
            identifications[0],
            {"related_date": self.parent.base_related_date},
        )
        self.assertEqual(
            identifications[1],
            {
                "type_code": "SKNB",
                "issuer": self.parent.sibling_issuer,
                "number": self.parent.sibling_number,
                "related_date": self.parent.sibling_related_date,
            },
        )
        self.assertEqual(second_line["line_type_code"], "SKNB")
        self.assertEqual(second_line["line_number"], self.parent.sibling_number)
        self.assertEqual(second_line["line_type_issuer"], self.parent.sibling_issuer)
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_without_second_line_description(
        self,
    ) -> list[dict[str, object]]:
        """Remove Description only from line two while retaining every neighboring field."""
        projection = deepcopy(self.base_projection)
        first_line, second_line = self._line_details(projection)
        if first_line.get("description") != self.first_line_description:
            raise AssertionError("first source line must retain its exact Description")
        if second_line.get("description") != self.second_line_description:
            raise AssertionError("second source line must begin with its exact Description")
        second_line.pop("description")
        return projection

    def _extract_second_line_description_line(self, payload: bytes) -> str:
        """Return the exact complete Description line from the second LineDtls member."""
        text = payload.decode("utf-8")
        segment, _, _ = self.parent._second_line_segment(text)
        marker = f"<Desc>{self.second_line_description}</Desc>"
        if segment.count(marker) != 1:
            raise AssertionError("second source line requires one exact Description")
        marker_pos = segment.find(marker)
        line_start = segment.rfind("\n", 0, marker_pos) + 1
        line_end = segment.find("\n", marker_pos + len(marker))
        if line_end < 0:
            raise AssertionError("Description must end before another source line")
        line = segment[line_start : line_end + 1]
        if line.strip() != marker:
            raise AssertionError("Description line may contain indentation but no other field")
        return line

    def _remove_second_line_description(self, payload: bytes) -> bytes:
        """Remove only the exact Description line from the second LineDtls member."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent._second_line_segment(text)
        if segment.count(self.description_line) != 1:
            raise AssertionError("second source line must contain its exact Description line once")
        if f"<Desc>{self.first_line_description}</Desc>" in segment:
            raise AssertionError("second source line must not contain the first line Description")
        changed_segment = segment.replace(self.description_line, "", 1)
        return (text[:segment_start] + changed_segment + text[segment_end:]).encode(
            "utf-8"
        )

    def _restore_second_line_description(self, payload: bytes) -> bytes:
        """Restore the exact removed Description bytes before the direct line Amount group."""
        text = payload.decode("utf-8")
        segment, segment_start, segment_end = self.parent._second_line_segment(text)
        if "<Desc>" in segment or "</Desc>" in segment:
            raise AssertionError("description-absence fixture must omit Description on line two")

        lines = segment.splitlines(keepends=True)
        amount_line_indexes = [
            index
            for index, line in enumerate(lines)
            if line.strip() == "<Amt>"
        ]
        if len(amount_line_indexes) != 1:
            raise AssertionError("second source line requires one direct Amount group")
        insert_index = amount_line_indexes[0]
        restored_segment = "".join(
            lines[:insert_index]
            + [self.description_line]
            + lines[insert_index:]
        )
        return (text[:segment_start] + restored_segment + text[segment_end:]).encode(
            "utf-8"
        )

    @staticmethod
    def _line_details(
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return the exact two source-ordered LineDtls mappings from one document."""
        if len(projection) != 1:
            raise AssertionError("Description-absence RED requires one referred document")
        line_details = projection[0].get("line_details")
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("Description-absence RED requires two source lines")
        if not all(isinstance(line, dict) for line in line_details):
            raise AssertionError("every LineDtls projection must be a mapping")
        return line_details[0], line_details[1]

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


if __name__ == "__main__":
    unittest.main()
