"""REDs for member-local Number inside repeated referred-document line Identifications."""

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
    test_postgres_bank_statement_structured_referred_document_repeated_line_identification_proprietary_evidence_red
    as proprietary_contract,
)

_PARENT_TEST = (
    proprietary_contract.BankStatementStructuredRepeatedLineIdentificationProprietaryEvidenceRedTests
)
_ISSUER_PARENT_TEST = proprietary_contract._PARENT_TEST
_CORRECTION_ERROR = proprietary_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredRepeatedLineIdentificationNumberEvidenceRedTests(
    unittest.TestCase
):
    """Retain Number on the exact repeated line-identification member that supplied it."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-Id proprietary-Type fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Mutate only the first repeated member Number while holding its other fields."""
        self.parent = _PARENT_TEST(
            "test_repeated_identification_proprietary_type_is_material_to_each_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_number = self._first_repeated_number(self.parent.base_projection)
        self.changed_number = f"{self.base_number}-R"
        self.base_payload = self.parent.base_payload
        self.changed_payload = self._replace_first_repeated_number(
            self.base_payload,
            self.base_number,
            self.changed_number,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = deepcopy(self.parent.base_projection)
        self.changed_projection = self._projection_with_first_repeated_number(
            self.changed_number
        )

    def test_repeated_identification_number_is_material_to_each_hash(self) -> None:
        """Bind a member-local Number-only source change to every evidence hash."""
        self.assertEqual(
            self._replace_first_repeated_number(
                self.changed_payload,
                self.changed_number,
                self.base_number,
            ),
            self.base_payload,
        )
        _ISSUER_PARENT_TEST._assert_non_structured_normalization_unchanged(self)

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
        self.assertNotEqual(
            expected_base_artifact_hash,
            expected_changed_artifact_hash,
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

    def test_rejected_repeated_identification_number_change_preserves_all_evidence(
        self,
    ) -> None:
        """Reject a Number-only replay without relational or raw-artifact residue."""
        restricted_owner = self.parent.parent.parent.parent
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "repeated-line-identification-number-base",
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
                    "repeated-line-identification-number-changed",
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

    def test_buyer_read_keeps_number_bound_to_exact_repeated_member(self) -> None:
        """Expose the changed Number only on the first repeated identification."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "repeated-line-identification-number-lookup",
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
        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.changed_projection,
        )

        second_line = detail[_STRUCTURED_EVIDENCE_KEY][0]["line_details"][1]
        self.assertEqual(second_line["line_type_code"], "SKNB")
        self.assertEqual(
            second_line["line_number"],
            self.line_contract.second_line_number,
        )
        self.assertEqual(
            second_line["line_type_issuer"],
            self.parent.parent.sknb_issuer,
        )
        self.assertEqual(
            second_line["line_identifications"],
            [
                {
                    "type_proprietary": self.parent.base_proprietary_type,
                    "issuer": self.parent.parent.prnb_issuer,
                    "number": self.changed_number,
                    "related_date": self._first_repeated_related_date(
                        self.parent.base_projection
                    ),
                },
                {
                    "type_code": "SKNB",
                    "issuer": self.parent.parent.sknb_issuer,
                    "number": self.line_contract.second_line_number,
                    "related_date": self.line_contract.line_related_date,
                },
            ],
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_with_first_repeated_number(
        self,
        number: str,
    ) -> list[dict[str, object]]:
        """Change only Number on the proprietary first repeated identification."""
        projection = deepcopy(self.parent.base_projection)
        first_member, second_member = self._repeated_members(projection)
        if first_member.get("type_proprietary") != self.parent.base_proprietary_type:
            raise AssertionError("first repeated Id must retain proprietary Type")
        if first_member.get("issuer") != self.parent.parent.prnb_issuer:
            raise AssertionError("first repeated Id must retain PARTS-SCHEME issuer")
        if first_member.get("number") != self.base_number:
            raise AssertionError("first repeated Id must begin with canonical Number")
        first_member["number"] = number
        if second_member.get("type_code") != "SKNB":
            raise AssertionError("second repeated Id must remain canonical SKNB")
        if second_member.get("number") != self.line_contract.second_line_number:
            raise AssertionError("second repeated Id Number must remain canonical")
        return projection

    def _replace_first_repeated_number(
        self,
        payload: bytes,
        current_number: str,
        replacement_number: str,
    ) -> bytes:
        """Replace Number only on the proprietary first repeated Id member."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = (
            self.parent.parent.parent.parent._second_line_segment(text)
        )
        target_type = self.parent.base_proprietary_type
        target_issuer = self.parent.parent.prnb_issuer
        target_date = self._first_repeated_related_date(self.parent.base_projection)
        starts: list[int] = []
        cursor = 0
        while True:
            start = line_segment.find("<Id>", cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len("<Id>")
        if len(starts) != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("repeated-Id Number RED requires exactly two Id blocks")

        matches: list[tuple[int, int, str]] = []
        for start in starts:
            end_start = line_segment.find("</Id>", start)
            if end_start < 0:
                raise AssertionError("every Id block requires a closing tag")
            end = end_start + len("</Id>")
            block = line_segment[start:end]
            if (
                f"<Prtry>{target_type}</Prtry>" in block
                and f"<Issr>{target_issuer}</Issr>" in block
                and f"<RltdDt>{target_date}</RltdDt>" in block
            ):
                matches.append((start, end, block))
        if len(matches) != 1:
            raise AssertionError("proprietary first repeated Id must be unique")

        start, end, block = matches[0]
        current_markup = f"<Nb>{current_number}</Nb>"
        replacement_markup = f"<Nb>{replacement_number}</Nb>"
        if block.count(current_markup) != 1:
            raise AssertionError("target repeated Id Number must occur exactly once")
        if current_markup != replacement_markup and replacement_markup in block:
            raise AssertionError("replacement repeated Id Number must not pre-exist")
        changed_block = block.replace(current_markup, replacement_markup, 1)
        changed_line = line_segment[:start] + changed_block + line_segment[end:]
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    @staticmethod
    def _repeated_members(
        projection: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Return the two repeated identification mappings from the second line."""
        if len(projection) != 1:
            raise AssertionError("repeated-Id Number RED requires one referred document")
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("repeated-Id Number RED requires two source lines")
        second_line = lines[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second source line projection must be a mapping")
        identifications = second_line.get("line_identifications")
        if not isinstance(identifications, list) or len(identifications) != 2:
            raise AssertionError("repeated-Id Number RED requires exactly two Ids")
        first_member = identifications[0]
        second_member = identifications[1]
        if not isinstance(first_member, dict) or not isinstance(second_member, dict):
            raise AssertionError("repeated Id projections must be mappings")
        return first_member, second_member

    def _first_repeated_number(
        self,
        projection: list[dict[str, object]],
    ) -> str:
        """Read the canonical first repeated identification Number."""
        first_member, _ = self._repeated_members(projection)
        number = first_member.get("number")
        if not isinstance(number, str) or not number:
            raise AssertionError("first repeated Id requires a non-empty Number")
        return number

    def _first_repeated_related_date(
        self,
        projection: list[dict[str, object]],
    ) -> str:
        """Read the canonical first repeated identification Related Date."""
        first_member, _ = self._repeated_members(projection)
        related_date = first_member.get("related_date")
        if not isinstance(related_date, str) or not related_date:
            raise AssertionError("first repeated Id requires a Related Date")
        return related_date


if __name__ == "__main__":
    unittest.main()
