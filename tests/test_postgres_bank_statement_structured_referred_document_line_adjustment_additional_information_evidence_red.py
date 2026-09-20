"""REDs for line-level adjustment additional-information evidence preservation."""

from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_adjustment_reason_evidence_red
    as reason_contract,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineAdjustmentAdditionalInformationEvidenceRedTests(
    unittest.TestCase
):
    """Retain line adjustment additional information with exact tuple and ordinal."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        reason_contract.BankStatementStructuredLineAdjustmentReasonEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements differing only in later adjustment additional information."""
        self.reason_contract = (
            reason_contract.BankStatementStructuredLineAdjustmentReasonEvidenceRedTests(
                "test_line_adjustment_reason_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.reason_contract.setUp()
        self.addCleanup(self.reason_contract.doCleanups)
        self.credit_debit_contract = self.reason_contract.contract

        self.base_additional_information = "Processing fee"
        self.changed_additional_information = "Processing fee correction"
        self.base_payload = self.reason_contract.base_payload
        self.changed_payload = self._replace_second_line_later_adjustment_additional_information(
            self.base_payload,
            self.base_additional_information,
            self.changed_additional_information,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

    def test_line_adjustment_additional_information_is_material_to_each_canonical_evidence_hash(
        self,
    ) -> None:
        """Bind an additional-information-only source change to the evidence chain."""
        self.assertEqual(
            self._replace_second_line_later_adjustment_additional_information(
                self.changed_payload,
                self.changed_additional_information,
                self.base_additional_information,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(self.base_additional_information)
        changed_projection = self._structured_projection(self.changed_additional_information)
        line_contract = self.credit_debit_contract.line_contract

        self.assertNotEqual(base_projection, changed_projection)
        self._assert_non_structured_normalization_unchanged()
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

    def test_changed_line_adjustment_additional_information_fails_closed_for_same_statement_identity(
        self,
    ) -> None:
        """Additional-information-only changes fail closed without correction authority."""
        accepted = accept_bank_statement_evidence(
            self.credit_debit_contract._command(
                self.base_payload,
                "line-adjustment-additional-information-base",
            ),
            posting.DATABASE_URL,
            self.credit_debit_contract.case.policy.tenant_reference,
            artifact_store=self.credit_debit_contract.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.credit_debit_contract._command(
                    self.changed_payload,
                    "line-adjustment-additional-information-changed",
                ),
                posting.DATABASE_URL,
                self.credit_debit_contract.case.policy.tenant_reference,
                artifact_store=self.credit_debit_contract.store,
            )

    def test_buyer_read_keeps_adjustment_additional_information_bound_to_exact_tuple(
        self,
    ) -> None:
        """Buyer reads retain free text on the exact later second-line adjustment."""
        accepted = accept_bank_statement_evidence(
            self.credit_debit_contract._command(
                self.changed_payload,
                "line-adjustment-additional-information-lookup",
            ),
            posting.DATABASE_URL,
            self.credit_debit_contract.case.policy.tenant_reference,
            artifact_store=self.credit_debit_contract.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.credit_debit_contract.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]

        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self._structured_projection(self.changed_additional_information),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove the source delta cannot hide collateral normalized-field changes."""
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
        self.assertEqual(len(self.base_statement.entries), len(self.changed_statement.entries))

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
            self.assertEqual(len(base_entry.entry_details), len(changed_entry.entry_details))
            for base_detail, changed_detail in zip(
                base_entry.entry_details,
                changed_entry.entry_details,
                strict=True,
            ):
                self.assertEqual(
                    tuple(getattr(base_detail, field) for field in detail_fields),
                    tuple(getattr(changed_detail, field) for field in detail_fields),
                )

    def _structured_projection(
        self,
        second_line_later_additional_information: str,
    ) -> list[dict[str, object]]:
        """Return the parent projection with only later adjustment free text changed."""
        projection = deepcopy(
            self.reason_contract._structured_projection(
                self.reason_contract.base_reason_code
            )
        )
        line_details = projection[0]["line_details"]
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("canonical line projection must contain exactly two lines")
        second_line = line_details[1]
        if not isinstance(second_line, dict):
            raise AssertionError("second line projection must be one mapping")
        adjustments = second_line.get("adjustments")
        if not isinstance(adjustments, list) or len(adjustments) != 2:
            raise AssertionError("second line projection must contain exactly two adjustments")
        later_adjustment = adjustments[1]
        if not isinstance(later_adjustment, dict):
            raise AssertionError("later second-line adjustment must be one mapping")
        if later_adjustment.get("additional_information") != self.base_additional_information:
            raise AssertionError(
                "parent projection must retain baseline Processing fee additional information"
            )
        later_adjustment["additional_information"] = (
            second_line_later_additional_information
        )
        return projection

    def _replace_second_line_later_adjustment_additional_information(
        self,
        payload: bytes,
        old_additional_information: str,
        new_additional_information: str,
    ) -> bytes:
        """Replace only additional information on the second source adjustment."""
        text = payload.decode("utf-8")
        line_marker = f"<Nb>{self.credit_debit_contract.second_line_number}</Nb>"
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

        amount_text = format(
            self.credit_debit_contract.second_line_later_adjustment_amount,
            "f",
        )
        old_block = (
            "<AdjstmntAmtAndRsn>\n"
            f'                        <Amt Ccy="{self.credit_debit_contract.adjustment_currency_code}">{amount_text}</Amt>\n'
            f"                        <CdtDbtInd>{self.credit_debit_contract.base_credit_debit_code}</CdtDbtInd>\n"
            f"                        <Rsn>{self.reason_contract.base_reason_code}</Rsn>\n"
            f"                        <AddtlInf>{old_additional_information}</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>"
        )
        new_block = (
            "<AdjstmntAmtAndRsn>\n"
            f'                        <Amt Ccy="{self.credit_debit_contract.adjustment_currency_code}">{amount_text}</Amt>\n'
            f"                        <CdtDbtInd>{self.credit_debit_contract.base_credit_debit_code}</CdtDbtInd>\n"
            f"                        <Rsn>{self.reason_contract.base_reason_code}</Rsn>\n"
            f"                        <AddtlInf>{new_additional_information}</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>"
        )
        if line_segment.count(old_block) != 1:
            raise AssertionError("target second-line later adjustment must occur exactly once")
        if line_segment.index(old_block) != second_adjustment_index:
            raise AssertionError("target adjustment must be the second source adjustment")
        if old_block != new_block and line_segment.count(new_block) != 0:
            raise AssertionError("replacement adjustment information must not pre-exist")

        changed_line = line_segment.replace(old_block, new_block, 1)
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
