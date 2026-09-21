"""REDs for proprietary Type association inside repeated referred-document line Ids."""

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
    test_postgres_bank_statement_structured_referred_document_line_identification_issuer_population_evidence_red
    as issuer_contract,
)

_PARENT_TEST = (
    issuer_contract.BankStatementStructuredLineIdentificationIssuerPopulationEvidenceRedTests
)
_CORRECTION_ERROR = issuer_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredRepeatedLineIdentificationProprietaryEvidenceRedTests(
    unittest.TestCase
):
    """Retain a proprietary Type choice on the exact repeated Id member that supplied it."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-Id issuer fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Convert only the PRNB member to a proprietary Type, then mutate its value."""
        self.parent = _PARENT_TEST(
            "test_repeated_identification_issuer_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_proprietary_type = "PART_COMPONENT"
        self.changed_proprietary_type = "PART_COMPONENT_LOCAL"
        self.base_payload = self._replace_prnb_type_choice(
            self.parent.base_payload,
            self._coded_type_markup(
                self.parent.parent.parent.second_identification_type_code
            ),
            self._proprietary_type_markup(self.base_proprietary_type),
        )
        self.changed_payload = self._replace_prnb_type_choice(
            self.base_payload,
            self._proprietary_type_markup(self.base_proprietary_type),
            self._proprietary_type_markup(self.changed_proprietary_type),
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = self._projection_with_prnb_proprietary_type(
            self.base_proprietary_type
        )
        self.changed_projection = self._projection_with_prnb_proprietary_type(
            self.changed_proprietary_type
        )

    def test_repeated_identification_proprietary_type_is_material_to_each_hash(
        self,
    ) -> None:
        """Bind one member-local proprietary Type change to every canonical evidence hash."""
        self.assertEqual(
            self._replace_prnb_type_choice(
                self.changed_payload,
                self._proprietary_type_markup(self.changed_proprietary_type),
                self._proprietary_type_markup(self.base_proprietary_type),
            ),
            self.base_payload,
        )
        self.assertEqual(
            self._replace_prnb_type_choice(
                self.base_payload,
                self._proprietary_type_markup(self.base_proprietary_type),
                self._coded_type_markup(
                    self.parent.parent.parent.second_identification_type_code
                ),
            ),
            self.parent.base_payload,
        )
        _PARENT_TEST._assert_non_structured_normalization_unchanged(self)

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

    def test_rejected_repeated_identification_type_change_preserves_all_evidence(
        self,
    ) -> None:
        """Reject a proprietary-Type-only replay without relational or artifact residue."""
        restricted_owner = self.parent.parent.parent
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "repeated-line-identification-proprietary-base",
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
                    "repeated-line-identification-proprietary-changed",
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

    def test_buyer_read_keeps_proprietary_type_bound_to_exact_repeated_member(
        self,
    ) -> None:
        """Expose the changed proprietary Type only on the PRNB-origin member."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "repeated-line-identification-proprietary-lookup",
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
        self.assertEqual(second_line["line_type_issuer"], self.parent.sknb_issuer)
        self.assertEqual(
            second_line["line_identifications"],
            [
                {
                    "type_proprietary": self.changed_proprietary_type,
                    "issuer": self.parent.prnb_issuer,
                    "number": self.parent.parent.parent.second_identification_number,
                    "related_date": (
                        self.parent.parent.parent.second_identification_related_date
                    ),
                },
                {
                    "type_code": "SKNB",
                    "issuer": self.parent.sknb_issuer,
                    "number": self.line_contract.second_line_number,
                    "related_date": self.line_contract.line_related_date,
                },
            ],
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_with_prnb_proprietary_type(
        self,
        proprietary_type: str,
    ) -> list[dict[str, object]]:
        """Change only the first repeated member from coded to proprietary Type."""
        projection = deepcopy(self.parent.base_projection)
        if len(projection) != 1:
            raise AssertionError("proprietary repeated-Id RED requires one referred document")
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("proprietary repeated-Id RED requires two source lines")
        second_line = lines[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second source line projection must be a mapping")
        identifications = second_line.get("line_identifications")
        if not isinstance(identifications, list) or len(identifications) != 2:
            raise AssertionError("proprietary repeated-Id RED requires exactly two Ids")
        first_member = identifications[0]
        second_member = identifications[1]
        if not isinstance(first_member, dict) or not isinstance(second_member, dict):
            raise AssertionError("repeated Id projections must be mappings")
        if first_member.pop("type_code", None) != (
            self.parent.parent.parent.second_identification_type_code
        ):
            raise AssertionError("first repeated member must begin as canonical PRNB")
        first_member["type_proprietary"] = proprietary_type
        if second_member.get("type_code") != "SKNB":
            raise AssertionError("second repeated member must remain canonical SKNB")
        self.assertEqual(second_line.get("line_type_code"), "SKNB")
        self.assertEqual(second_line.get("line_type_issuer"), self.parent.sknb_issuer)
        return projection

    def _replace_prnb_type_choice(
        self,
        payload: bytes,
        current_markup: str,
        replacement_markup: str,
    ) -> bytes:
        """Replace Type choice only in the Id identified by Part-002/date/issuer."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = (
            self.parent.parent.parent._second_line_segment(text)
        )
        target_number = self.parent.parent.parent.second_identification_number
        target_date = self.parent.parent.parent.second_identification_related_date
        target_issuer = self.parent.prnb_issuer
        starts: list[int] = []
        cursor = 0
        while True:
            start = line_segment.find("<Id>", cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len("<Id>")
        if len(starts) != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("proprietary repeated-Id RED requires exactly two Id blocks")

        matches: list[tuple[int, int, str]] = []
        for start in starts:
            end_start = line_segment.find("</Id>", start)
            if end_start < 0:
                raise AssertionError("every Id block requires a closing tag")
            end = end_start + len("</Id>")
            block = line_segment[start:end]
            if (
                f"<Nb>{target_number}</Nb>" in block
                and f"<RltdDt>{target_date}</RltdDt>" in block
                and f"<Issr>{target_issuer}</Issr>" in block
            ):
                matches.append((start, end, block))
        if len(matches) != 1:
            raise AssertionError("exact PRNB-origin member must be uniquely identifiable")

        start, end, block = matches[0]
        if block.count(current_markup) != 1:
            raise AssertionError("target Type choice must occur exactly once")
        if current_markup != replacement_markup and replacement_markup in block:
            raise AssertionError("replacement Type choice must not pre-exist")
        changed_block = block.replace(current_markup, replacement_markup, 1)
        changed_line = line_segment[:start] + changed_block + line_segment[end:]
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    @staticmethod
    def _coded_type_markup(type_code: str) -> str:
        """Return the exact canonical coded-Type markup used by the fixture."""
        return (
            "<CdOrPrtry>\n"
            f"                          <Cd>{type_code}</Cd>\n"
            "                        </CdOrPrtry>"
        )

    @staticmethod
    def _proprietary_type_markup(value: str) -> str:
        """Return schema-equivalent proprietary-Type markup for one repeated Id."""
        return (
            "<CdOrPrtry>\n"
            f"                          <Prtry>{value}</Prtry>\n"
            "                        </CdOrPrtry>"
        )


if __name__ == "__main__":
    unittest.main()
