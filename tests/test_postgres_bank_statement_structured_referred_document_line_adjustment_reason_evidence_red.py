"""REDs for line-level adjustment reason evidence preservation."""

from __future__ import annotations

import unittest
from copy import deepcopy

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_adjustment_credit_debit_evidence_red
    as credit_debit_contract,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineAdjustmentReasonEvidenceRedTests(unittest.TestCase):
    """Retain each line adjustment reason with its exact source tuple and ordinal."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        credit_debit_contract.BankStatementStructuredLineAdjustmentCreditDebitEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements differing only in the later second-line adjustment reason."""
        self.contract = (
            credit_debit_contract.BankStatementStructuredLineAdjustmentCreditDebitEvidenceRedTests(
                "test_line_adjustment_indicator_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.contract.setUp()
        self.addCleanup(self.contract.doCleanups)

        self.base_reason_code = "FEES"
        self.changed_reason_code = "COMM"
        self.base_payload = self.contract.base_payload
        self.changed_payload = self._replace_second_line_later_adjustment_reason(
            self.base_payload,
            self.base_reason_code,
            self.changed_reason_code,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

    def test_line_adjustment_reason_is_material_to_each_canonical_evidence_hash(
        self,
    ) -> None:
        """Bind one reason-only source change to detail, entry, and statement evidence."""
        self.assertEqual(
            self._replace_second_line_later_adjustment_reason(
                self.changed_payload,
                self.changed_reason_code,
                self.base_reason_code,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(self.base_reason_code)
        changed_projection = self._structured_projection(self.changed_reason_code)
        line_contract = self.contract.line_contract

        self.assertNotEqual(base_projection, changed_projection)
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
            line_contract._assert_sha256(value)

        self.assertEqual(
            base_detail.source_detail_hash,
            line_contract._expected_detail_hash(base_detail, base_projection),
        )
        self.assertEqual(
            changed_detail.source_detail_hash,
            line_contract._expected_detail_hash(changed_detail, changed_projection),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            line_contract._expected_entry_hash(base_entry, {1: base_projection}),
        )
        self.assertEqual(
            changed_entry.source_entry_hash,
            line_contract._expected_entry_hash(changed_entry, {1: changed_projection}),
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            line_contract._expected_statement_hash(
                self.base_statement,
                {(1, 1): base_projection},
            ),
        )
        self.assertEqual(
            self.changed_statement.normalized_payload_hash,
            line_contract._expected_statement_hash(
                self.changed_statement,
                {(1, 1): changed_projection},
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
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            line_contract._expected_entry_hash(self.base_statement.entries[1], {}),
        )
        line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        line_contract._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_changed_line_adjustment_reason_fails_closed_for_same_statement_identity(
        self,
    ) -> None:
        """Reason-only changes fail closed while no statement correction contract exists."""
        accepted = accept_bank_statement_evidence(
            self.contract._command(self.base_payload, "line-adjustment-reason-base"),
            posting.DATABASE_URL,
            self.contract.case.policy.tenant_reference,
            artifact_store=self.contract.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.contract._command(
                    self.changed_payload,
                    "line-adjustment-reason-changed",
                ),
                posting.DATABASE_URL,
                self.contract.case.policy.tenant_reference,
                artifact_store=self.contract.store,
            )

    def test_buyer_read_keeps_adjustment_reason_bound_to_exact_line_and_tuple(
        self,
    ) -> None:
        """Buyer reads retain the reason with its exact second-line adjustment tuple."""
        accepted = accept_bank_statement_evidence(
            self.contract._command(
                self.changed_payload,
                "line-adjustment-reason-lookup",
            ),
            posting.DATABASE_URL,
            self.contract.case.policy.tenant_reference,
            artifact_store=self.contract.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.contract.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]

        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self._structured_projection(self.changed_reason_code),
        )
        self.assertEqual(str(entry["entry_amount"]), "25000.000000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(str(detail["detail_amount"]), "25000.000000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _structured_projection(self, second_line_later_reason_code: str) -> list[dict[str, object]]:
        """Return the parent projection with only the later reason changed."""
        projection = deepcopy(
            self.contract._structured_projection(self.contract.base_credit_debit_code)
        )
        line_details = projection[0]["line_details"]
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("canonical line projection must contain exactly two lines")
        adjustments = line_details[1].get("adjustments")
        if not isinstance(adjustments, list) or len(adjustments) != 2:
            raise AssertionError("second line projection must contain exactly two adjustments")
        later_adjustment = adjustments[1]
        if not isinstance(later_adjustment, dict):
            raise AssertionError("later second-line adjustment must be one mapping")
        if later_adjustment.get("reason_code") != self.base_reason_code:
            raise AssertionError("parent projection must retain the baseline FEES reason")
        later_adjustment["reason_code"] = second_line_later_reason_code
        return projection

    def _replace_second_line_later_adjustment_reason(
        self,
        payload: bytes,
        old_reason_code: str,
        new_reason_code: str,
    ) -> bytes:
        """Replace only the second source adjustment's reason on the second line."""
        text = payload.decode("utf-8")
        line_marker = f"<Nb>{self.contract.second_line_number}</Nb>"
        if text.count(line_marker) != 1:
            raise AssertionError("second line number marker must occur exactly once")
        marker_index = text.index(line_marker)
        line_start = text.rfind("<LineDtls>", 0, marker_index)
        line_end_start = text.find("</LineDtls>", marker_index)
        if line_start < 0 or line_end_start < 0:
            raise AssertionError("second line boundaries must be present")
        line_end = line_end_start + len("</LineDtls>")
        line_segment = text[line_start:line_end]
        adjustment_marker = "<AdjstmntAmtAndRsn>"
        if line_segment.count(adjustment_marker) != 2:
            raise AssertionError("second line must contain exactly two adjustments")
        first_adjustment_index = line_segment.index(adjustment_marker)
        second_adjustment_index = line_segment.index(
            adjustment_marker,
            first_adjustment_index + len(adjustment_marker),
        )

        amount_text = format(self.contract.second_line_later_adjustment_amount, "f")
        old_block = (
            "<AdjstmntAmtAndRsn>\n"
            f'                        <Amt Ccy="{self.contract.adjustment_currency_code}">{amount_text}</Amt>\n'
            f"                        <CdtDbtInd>{self.contract.base_credit_debit_code}</CdtDbtInd>\n"
            f"                        <Rsn>{old_reason_code}</Rsn>\n"
            "                        <AddtlInf>Processing fee</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>"
        )
        new_block = (
            "<AdjstmntAmtAndRsn>\n"
            f'                        <Amt Ccy="{self.contract.adjustment_currency_code}">{amount_text}</Amt>\n'
            f"                        <CdtDbtInd>{self.contract.base_credit_debit_code}</CdtDbtInd>\n"
            f"                        <Rsn>{new_reason_code}</Rsn>\n"
            "                        <AddtlInf>Processing fee</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>"
        )
        if line_segment.count(old_block) != 1:
            raise AssertionError("target second-line later adjustment must occur exactly once")
        if line_segment.index(old_block) != second_adjustment_index:
            raise AssertionError("target adjustment must be the second source adjustment")
        if old_block != new_block and line_segment.count(new_block) != 0:
            raise AssertionError("replacement adjustment reason must not pre-exist")

        changed_line = line_segment.replace(old_block, new_block, 1)
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
