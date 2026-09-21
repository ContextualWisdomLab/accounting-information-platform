"""REDs for repeated referred-document line-identification preservation."""

from __future__ import annotations

import hashlib
import unittest
from copy import deepcopy
from decimal import Decimal

import psycopg

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
    test_postgres_bank_statement_structured_referred_document_line_identification_code_evidence_red
    as code_contract,
)

_PARENT_TEST = code_contract.BankStatementStructuredLineIdentificationCodeEvidenceRedTests
_CORRECTION_ERROR = code_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineIdentificationCardinalityEvidenceRedTests(
    unittest.TestCase
):
    """Retain every LineDtls/Id member rather than collapsing to the first identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL line-identification fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Add one distinct second identification to the canonical second source line."""
        self.parent = _PARENT_TEST(
            "test_line_identification_code_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.second_identification_type_code = "PRNB"
        self.second_identification_number = "Part-002"
        self.base_payload = self.parent.base_payload
        self.changed_payload = self._insert_second_identification(self.base_payload)
        self.base_statement = self.parent.base_statement
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = self.parent._structured_projection(
            self.parent.base_line_type_code
        )
        self.changed_projection = self._projection_with_repeated_identification()

    def test_repeated_line_identification_is_material_to_each_evidence_hash(self) -> None:
        """Bind one additional Id member to raw, detail, entry, and statement identity."""
        self.assertEqual(
            self._remove_second_identification(self.changed_payload),
            self.base_payload,
        )
        self._assert_non_structured_normalization_unchanged()

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

    def test_rejected_extra_line_identification_preserves_all_accepted_evidence(self) -> None:
        """Fail closed without relational or raw-artifact residue for the extra identity."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-identification-cardinality-base",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])

        before_rows = self._tenant_statement_rows()
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
                    "line-identification-cardinality-changed",
                ),
                posting.DATABASE_URL,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(self._tenant_statement_rows(), before_rows)
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def test_buyer_read_retains_every_second_line_identification_in_source_order(
        self,
    ) -> None:
        """Expose both Id members while retaining the existing primary line fields."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-identification-cardinality-lookup",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
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
        second_line = detail[_STRUCTURED_EVIDENCE_KEY][0]["line_details"][1]
        self.assertEqual(second_line["line_type_code"], "SKNB")
        self.assertEqual(
            second_line["line_number"],
            self.line_contract.second_line_number,
        )
        self.assertEqual(
            second_line["line_identifications"],
            [
                {
                    "type_code": "SKNB",
                    "number": self.line_contract.second_line_number,
                    "related_date": self.line_contract.line_related_date,
                },
                {
                    "type_code": self.second_identification_type_code,
                    "number": self.second_identification_number,
                    "related_date": self.line_contract.line_related_date,
                },
            ],
        )
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

    def _projection_with_repeated_identification(self) -> list[dict[str, object]]:
        """Add the complete repeated Id population without changing primary line fields."""
        projection = deepcopy(self.base_projection)
        documents = projection
        if len(documents) != 1:
            raise AssertionError("cardinality RED requires one referred document")
        lines = documents[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("cardinality RED requires exactly two source lines")
        second_line = lines[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second source line projection must be a mapping")
        if second_line.get("line_type_code") != "SKNB":
            raise AssertionError("primary second-line type must remain SKNB")
        if second_line.get("line_number") != self.line_contract.second_line_number:
            raise AssertionError("primary second-line number must remain canonical")
        second_line["line_identifications"] = [
            {
                "type_code": "SKNB",
                "number": self.line_contract.second_line_number,
                "related_date": self.line_contract.line_related_date,
            },
            {
                "type_code": self.second_identification_type_code,
                "number": self.second_identification_number,
                "related_date": self.line_contract.line_related_date,
            },
        ]
        return projection

    def _insert_second_identification(self, payload: bytes) -> bytes:
        """Insert one complete PRNB Id after the exact primary Id in Stockitem2."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self._second_line_segment(text)
        if line_segment.count("<Id>") != 1 or line_segment.count("</Id>") != 1:
            raise AssertionError("base second source line must contain exactly one Id")
        if self.second_identification_number in line_segment:
            raise AssertionError("second identification must not pre-exist")
        first_id_end = line_segment.index("</Id>") + len("</Id>")
        second_id = self._second_identification_markup()
        changed_line = (
            line_segment[:first_id_end]
            + "\n"
            + second_id
            + line_segment[first_id_end:]
        )
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _remove_second_identification(self, payload: bytes) -> bytes:
        """Remove only the exact inserted PRNB Id and restore the original bytes."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self._second_line_segment(text)
        second_id = "\n" + self._second_identification_markup()
        if line_segment.count(second_id) != 1:
            raise AssertionError("inserted second identification must occur exactly once")
        if line_segment.count("<Id>") != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("changed second source line must contain exactly two Ids")
        restored_line = line_segment.replace(second_id, "", 1)
        return (text[:line_start] + restored_line + text[line_end:]).encode("utf-8")

    def _second_line_segment(self, text: str) -> tuple[str, int, int]:
        """Return the exact Stockitem2 LineDtls slice and absolute boundaries."""
        line_marker = f"<Nb>{self.line_contract.second_line_number}</Nb>"
        if text.count(line_marker) != 1:
            raise AssertionError("second line number marker must occur exactly once")
        marker_index = text.index(line_marker)
        line_start = text.rfind("<LineDtls>", 0, marker_index)
        line_end_start = text.find("</LineDtls>", marker_index)
        if line_start < 0 or line_end_start < 0:
            raise AssertionError("second line boundaries must be present")
        line_end = line_end_start + len("</LineDtls>")
        return text[line_start:line_end], line_start, line_end

    def _second_identification_markup(self) -> str:
        """Return the complete additional source Id in schema-valid child order."""
        return (
            "                    <Id>\n"
            "                      <Tp>\n"
            "                        <CdOrPrtry>\n"
            f"                          <Cd>{self.second_identification_type_code}</Cd>\n"
            "                        </CdOrPrtry>\n"
            "                      </Tp>\n"
            f"                      <Nb>{self.second_identification_number}</Nb>\n"
            f"                      <RltdDt>{self.line_contract.line_related_date}</RltdDt>\n"
            "                    </Id>"
        )

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove the new source member cannot hide collateral normalized-field drift."""
        statement_fields = (
            "message_definition_identifier",
            "statement_identity_reference",
            "electronic_sequence_number",
            "legal_sequence_number",
            "period_start_at",
            "period_end_at",
            "opening_balance_hash",
            "closing_balance_hash",
            "account_currency_code",
            "account_identifier_hash",
        )
        self.assertEqual(
            tuple(getattr(self.base_statement, field) for field in statement_fields),
            tuple(getattr(self.changed_statement, field) for field in statement_fields),
        )
        self.assertEqual(
            len(self.base_statement.entries),
            len(self.changed_statement.entries),
        )

        entry_fields = (
            "source_entry_identity",
            "entry_sequence_number",
            "source_locator_path",
            "booking_occurred_at",
            "value_occurred_at",
            "entry_amount",
            "entry_currency_code",
            "credit_debit_code",
            "reversal_indicator",
            "bank_transaction_domain_code",
            "bank_transaction_family_code",
            "bank_transaction_subfamily_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "mandate_reference",
            "cheque_reference",
            "remittance_evidence_text",
            "counterparty_evidence_hash",
        )
        detail_fields = (
            "detail_sequence_number",
            "source_locator_path",
            "detail_amount",
            "detail_currency_code",
            "credit_debit_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "remittance_evidence_text",
        )
        for base_entry, changed_entry in zip(
            self.base_statement.entries,
            self.changed_statement.entries,
            strict=True,
        ):
            self.assertEqual(
                tuple(getattr(base_entry, field) for field in entry_fields),
                tuple(getattr(changed_entry, field) for field in entry_fields),
            )
            self.assertEqual(
                len(base_entry.entry_details),
                len(changed_entry.entry_details),
            )
            for base_detail, changed_detail in zip(
                base_entry.entry_details,
                changed_entry.entry_details,
                strict=True,
            ):
                self.assertEqual(
                    tuple(getattr(base_detail, field) for field in detail_fields),
                    tuple(getattr(changed_detail, field) for field in detail_fields),
                )

    def _tenant_statement_rows(self) -> dict[str, tuple[tuple[object, ...], ...]]:
        """Snapshot every evidence column after installing the tenant RLS context."""
        tenant_reference = self.line_contract.case.policy.tenant_reference
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_row = connection.execute(
                """
                SELECT tenant_account_id
                FROM accounting_core.tenant_account
                WHERE tenant_account_code = %s
                """,
                (tenant_reference,),
            ).fetchone()
            if tenant_row is None:
                raise AssertionError("test tenant must exist before evidence snapshot")
            tenant_id = tenant_row[0]
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(tenant_id),),
            )

            artifact_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_artifact
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_artifact_id
                """,
                (tenant_id,),
            ).fetchall()
            statement_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_record
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_record_id
                """,
                (tenant_id,),
            ).fetchall()
            entry_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_entry
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_entry_id
                """,
                (tenant_id,),
            ).fetchall()
            detail_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_entry_detail
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_entry_detail_id
                """,
                (tenant_id,),
            ).fetchall()

        return {
            "bank_statement_artifact": tuple(tuple(row) for row in artifact_rows),
            "bank_statement_record": tuple(tuple(row) for row in statement_rows),
            "bank_statement_entry": tuple(tuple(row) for row in entry_rows),
            "bank_statement_entry_detail": tuple(tuple(row) for row in detail_rows),
        }


if __name__ == "__main__":
    unittest.main()
