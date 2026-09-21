"""REDs for issuer association across repeated referred-document line Id members."""

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
    test_postgres_bank_statement_structured_referred_document_line_identification_order_evidence_red
    as order_contract,
)

_PARENT_TEST = order_contract.BankStatementStructuredLineIdentificationOrderEvidenceRedTests
_CORRECTION_ERROR = order_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineIdentificationIssuerPopulationEvidenceRedTests(
    unittest.TestCase
):
    """Keep each optional Id/Issr bound to the exact repeated Id member it qualifies."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL repeated-identification order fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Give both repeated Id members distinct issuers, then mutate only the PRNB issuer."""
        self.parent = _PARENT_TEST(
            "test_repeated_line_identification_order_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.prnb_issuer = "PARTS-SCHEME"
        self.changed_prnb_issuer = "PARTS-LOCAL"
        self.sknb_issuer = "STOCK-SCHEME"

        self.base_payload = self._with_repeated_identification_issuers(
            self.parent.changed_payload,
            self.prnb_issuer,
            self.sknb_issuer,
        )
        self.changed_payload = self._replace_prnb_issuer(
            self.base_payload,
            self.prnb_issuer,
            self.changed_prnb_issuer,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.base_projection = self._projection_with_identification_issuers(
            self.prnb_issuer
        )
        self.changed_projection = self._projection_with_identification_issuers(
            self.changed_prnb_issuer
        )

    def test_repeated_identification_issuer_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind one member-local issuer change to raw, detail, entry, and statement identity."""
        self.assertEqual(
            self._remove_repeated_identification_issuers(self.base_payload),
            self.parent.changed_payload,
        )
        self.assertEqual(
            self._replace_prnb_issuer(
                self.changed_payload,
                self.changed_prnb_issuer,
                self.prnb_issuer,
            ),
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

    def test_rejected_repeated_identification_issuer_change_preserves_all_evidence(
        self,
    ) -> None:
        """Reject an issuer-only replay without relational or immutable-artifact residue."""
        restricted_owner = self.parent.parent
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-identification-issuer-population-base",
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
                    "line-identification-issuer-population-changed",
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

    def test_buyer_read_keeps_issuer_bound_to_each_repeated_identification(
        self,
    ) -> None:
        """Expose distinct member issuers while preserving the canonical primary scalar issuer."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "line-identification-issuer-population-lookup",
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
            second_line["related_date"],
            self.line_contract.line_related_date,
        )
        self.assertEqual(second_line["line_type_issuer"], self.sknb_issuer)
        self.assertEqual(
            second_line["line_identifications"],
            [
                {
                    "type_code": self.parent.parent.second_identification_type_code,
                    "issuer": self.changed_prnb_issuer,
                    "number": self.parent.parent.second_identification_number,
                    "related_date": self.parent.parent.second_identification_related_date,
                },
                {
                    "type_code": "SKNB",
                    "issuer": self.sknb_issuer,
                    "number": self.line_contract.second_line_number,
                    "related_date": self.line_contract.line_related_date,
                },
            ],
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _projection_with_identification_issuers(
        self,
        prnb_issuer: str,
    ) -> list[dict[str, object]]:
        """Attach issuers to both repeated Id members without changing their source order."""
        projection = deepcopy(self.parent.changed_projection)
        if len(projection) != 1:
            raise AssertionError("issuer population RED requires one referred document")
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("issuer population RED requires exactly two source lines")
        second_line = lines[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second source line projection must be a mapping")
        identifications = second_line.get("line_identifications")
        if not isinstance(identifications, list) or len(identifications) != 2:
            raise AssertionError("issuer population RED requires exactly two Id members")
        if not all(isinstance(item, dict) for item in identifications):
            raise AssertionError("each repeated Id projection must be a mapping")

        expected_prnb = {
            "type_code": self.parent.parent.second_identification_type_code,
            "number": self.parent.parent.second_identification_number,
            "related_date": self.parent.parent.second_identification_related_date,
        }
        expected_sknb = {
            "type_code": "SKNB",
            "number": self.line_contract.second_line_number,
            "related_date": self.line_contract.line_related_date,
        }
        if identifications != [expected_prnb, expected_sknb]:
            raise AssertionError("parent source-order projection must remain PRNB then SKNB")

        identifications[0]["issuer"] = prnb_issuer
        identifications[1]["issuer"] = self.sknb_issuer
        second_line["line_type_issuer"] = self.sknb_issuer
        return projection

    def _with_repeated_identification_issuers(
        self,
        payload: bytes,
        prnb_issuer: str,
        sknb_issuer: str,
    ) -> bytes:
        """Insert one distinct Issr into each exact repeated Id block."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self.parent.parent._second_line_segment(text)
        blocks = self._identification_blocks(line_segment)
        expected_order = (
            self.parent.parent.second_identification_type_code,
            "SKNB",
        )
        if tuple(identity[0] for identity, _, _, _ in blocks) != expected_order:
            raise AssertionError("issuer fixture requires parent PRNB then SKNB source order")

        replacements = {
            self.parent.parent.second_identification_type_code: prnb_issuer,
            "SKNB": sknb_issuer,
        }
        cursor = 0
        changed_parts: list[str] = []
        for identity, start, end, block in blocks:
            changed_parts.append(line_segment[cursor:start])
            if "<Issr>" in block or "</Issr>" in block:
                raise AssertionError("parent repeated Id members must not pre-populate issuer")
            marker = (
                "                      </Tp>\n"
                "                      <Nb>"
            )
            if block.count(marker) != 1:
                raise AssertionError("Id Type/Number boundary must be unique before issuer insert")
            issuer = replacements[identity[0]]
            changed_block = block.replace(
                marker,
                (
                    "                      </Tp>\n"
                    f"                      <Issr>{issuer}</Issr>\n"
                    "                      <Nb>"
                ),
                1,
            )
            changed_parts.append(changed_block)
            cursor = end
        changed_parts.append(line_segment[cursor:])
        changed_line = "".join(changed_parts)
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _remove_repeated_identification_issuers(self, payload: bytes) -> bytes:
        """Remove exactly the two fixture issuers and recover the parent bytes."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self.parent.parent._second_line_segment(text)
        blocks = self._identification_blocks(line_segment)
        expected_issuers = {
            self.parent.parent.second_identification_type_code: self.prnb_issuer,
            "SKNB": self.sknb_issuer,
        }
        cursor = 0
        restored_parts: list[str] = []
        for identity, start, end, block in blocks:
            restored_parts.append(line_segment[cursor:start])
            issuer = expected_issuers[identity[0]]
            marker = f"                      <Issr>{issuer}</Issr>\n"
            if block.count(marker) != 1:
                raise AssertionError("each fixture Id must carry its exact expected issuer")
            restored_parts.append(block.replace(marker, "", 1))
            cursor = end
        restored_parts.append(line_segment[cursor:])
        restored_line = "".join(restored_parts)
        return (text[:line_start] + restored_line + text[line_end:]).encode("utf-8")

    def _replace_prnb_issuer(
        self,
        payload: bytes,
        old_issuer: str,
        new_issuer: str,
    ) -> bytes:
        """Replace only the PRNB member's Issr, leaving SKNB and source order untouched."""
        text = payload.decode("utf-8")
        line_segment, line_start, line_end = self.parent.parent._second_line_segment(text)
        blocks = self._identification_blocks(line_segment)
        target_type = self.parent.parent.second_identification_type_code
        matches = [
            (start, end, block)
            for identity, start, end, block in blocks
            if identity[0] == target_type
        ]
        if len(matches) != 1:
            raise AssertionError("PRNB target Id must occur exactly once")
        start, end, block = matches[0]
        old_marker = f"<Issr>{old_issuer}</Issr>"
        new_marker = f"<Issr>{new_issuer}</Issr>"
        if block.count(old_marker) != 1:
            raise AssertionError("target PRNB issuer must occur exactly once")
        if old_marker != new_marker and new_marker in block:
            raise AssertionError("replacement PRNB issuer must not pre-exist")
        changed_block = block.replace(old_marker, new_marker, 1)
        changed_line = line_segment[:start] + changed_block + line_segment[end:]
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _identification_blocks(
        self,
        line_segment: str,
    ) -> list[tuple[tuple[str, str, str], int, int, str]]:
        """Locate both complete Id blocks and retain exact source positions."""
        starts: list[int] = []
        cursor = 0
        while True:
            start = line_segment.find("<Id>", cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len("<Id>")
        if len(starts) != 2 or line_segment.count("</Id>") != 2:
            raise AssertionError("issuer population RED requires exactly two complete Ids")

        result: list[tuple[tuple[str, str, str], int, int, str]] = []
        previous_end = -1
        for start in starts:
            end = line_segment.find("</Id>", start)
            if end < 0:
                raise AssertionError("every Id must have a closing tag")
            end += len("</Id>")
            if start <= previous_end:
                raise AssertionError("Id blocks must not overlap")
            block = line_segment[start:end]
            identity = self.parent._identification_identity(block)
            result.append((identity, start, end, block))
            previous_end = end
        return result

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove the issuer-only delta cannot hide collateral normalized-field drift."""
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


if __name__ == "__main__":
    unittest.main()
