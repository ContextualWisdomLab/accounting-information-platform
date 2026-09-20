"""REDs for line-level adjustment currency evidence preservation."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_details_evidence_red
    as line_details_contract,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineAdjustmentCurrencyEvidenceRedTests(unittest.TestCase):
    """Retain each line adjustment currency with its exact source adjustment tuple."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two-line statements differing only in one adjustment currency."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.document_number = "INV-2026-1001"
        self.line_related_date = "2026-09-01"
        self.first_line_number = "Stockitem1"
        self.second_line_number = "Stockitem2"
        self.first_line_description = "Annual support service"
        self.second_line_description = "Quarterly data service"
        self.second_line_later_adjustment_amount = Decimal("50.00")
        self.base_adjustment_currency_code = "KRW"
        self.changed_adjustment_currency_code = "USD"

        self.line_contract = (
            line_details_contract.BankStatementStructuredLineDetailsEvidenceRedTests(
                "test_line_projection_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.line_contract.document_number = self.document_number
        self.line_contract.first_line_number = self.first_line_number
        self.line_contract.first_line_description = self.first_line_description
        self.line_contract.line_related_date = self.line_related_date

        line_payload = self.line_contract._with_line_details(
            fixture,
            marker,
            self.second_line_number,
            self.second_line_description,
        )
        self.base_payload = self._with_line_adjustments(line_payload)
        self.changed_payload = self._replace_second_line_later_adjustment_currency(
            self.base_payload,
            self.base_adjustment_currency_code,
            self.changed_adjustment_currency_code,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = (
            f"urn:cwl:bank_account:structured-line-adjustment-currency:"
            f"{uuid.uuid4().hex}"
        )
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.base_statement.account_currency_code,
                "account_identifier_hash": self.base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_line_adjustment_currency_is_material_to_each_canonical_evidence_hash(
        self,
    ) -> None:
        """Bind one adjustment-currency-only source change to the evidence chain."""
        self.assertEqual(
            self._replace_second_line_later_adjustment_currency(
                self.changed_payload,
                self.changed_adjustment_currency_code,
                self.base_adjustment_currency_code,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(
            self.base_adjustment_currency_code
        )
        changed_projection = self._structured_projection(
            self.changed_adjustment_currency_code
        )

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
            self.line_contract._assert_sha256(value)

        self.assertEqual(
            base_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(base_detail, base_projection),
        )
        self.assertEqual(
            changed_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(changed_detail, changed_projection),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(base_entry, {1: base_projection}),
        )
        self.assertEqual(
            changed_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(
                changed_entry,
                {1: changed_projection},
            ),
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.base_statement,
                {(1, 1): base_projection},
            ),
        )
        self.assertEqual(
            self.changed_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
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
            self.line_contract._expected_entry_hash(self.base_statement.entries[1], {}),
        )
        self.line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        self.line_contract._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_changed_line_adjustment_currency_requires_explicit_statement_correction(
        self,
    ) -> None:
        """Adjustment-currency-only changes cannot silently replay one statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "line-adjustment-currency-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(
                    self.changed_payload,
                    "line-adjustment-currency-changed",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_adjustment_currency_bound_to_exact_line_and_tuple(
        self,
    ) -> None:
        """Buyer reads retain adjustment currency with its exact source tuple."""
        accepted = accept_bank_statement_evidence(
            self._command(
                self.changed_payload,
                "line-adjustment-currency-lookup",
            ),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]

        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self._structured_projection(self.changed_adjustment_currency_code),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _structured_projection(
        self,
        second_line_later_adjustment_currency_code: str,
    ) -> list[dict[str, object]]:
        """Return the canonical projection with one isolated adjustment currency."""
        projection = self.line_contract._structured_projection(
            self.second_line_number,
            self.second_line_description,
        )
        line_details = projection[0]["line_details"]
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("canonical line projection must contain exactly two lines")

        line_details[0]["adjustments"] = [
            {
                "amount": "100",
                "currency_code": "KRW",
                "credit_debit_code": "DBIT",
                "reason_code": "DISC",
                "additional_information": "Service rebate correction",
            }
        ]
        line_details[1]["adjustments"] = [
            {
                "amount": "200",
                "currency_code": "KRW",
                "credit_debit_code": "CRDT",
                "reason_code": "ADJT",
                "additional_information": "Contract true-up",
            },
            {
                "amount": self.line_contract._decimal_text(
                    self.second_line_later_adjustment_amount
                ),
                "currency_code": second_line_later_adjustment_currency_code,
                "credit_debit_code": "DBIT",
                "reason_code": "FEES",
                "additional_information": "Processing fee",
            },
        ]
        return projection

    def _with_line_adjustments(self, line_payload: bytes) -> bytes:
        """Insert source-ordered line adjustments before each remitted amount."""
        text = line_payload.decode("utf-8")
        first_marker = '                      <RmtdAmt Ccy="KRW">9700.05</RmtdAmt>'
        second_marker = '                      <RmtdAmt Ccy="KRW">5000.10</RmtdAmt>'
        if text.count(first_marker) != 1 or text.count(second_marker) != 1:
            raise AssertionError("canonical line remitted-amount markers must be unique")

        first_adjustment = (
            "                      <AdjstmntAmtAndRsn>\n"
            '                        <Amt Ccy="KRW">100.00</Amt>\n'
            "                        <CdtDbtInd>DBIT</CdtDbtInd>\n"
            "                        <Rsn>DISC</Rsn>\n"
            "                        <AddtlInf>Service rebate correction</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>\n"
            f"{first_marker}"
        )
        second_adjustments = (
            "                      <AdjstmntAmtAndRsn>\n"
            '                        <Amt Ccy="KRW">200.00</Amt>\n'
            "                        <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "                        <Rsn>ADJT</Rsn>\n"
            "                        <AddtlInf>Contract true-up</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>\n"
            "                      <AdjstmntAmtAndRsn>\n"
            '                        <Amt Ccy="KRW">50.00</Amt>\n'
            "                        <CdtDbtInd>DBIT</CdtDbtInd>\n"
            "                        <Rsn>FEES</Rsn>\n"
            "                        <AddtlInf>Processing fee</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>\n"
            f"{second_marker}"
        )
        text = text.replace(first_marker, first_adjustment, 1)
        text = text.replace(second_marker, second_adjustments, 1)
        return text.encode("utf-8")

    def _replace_second_line_later_adjustment_currency(
        self,
        payload: bytes,
        old_currency_code: str,
        new_currency_code: str,
    ) -> bytes:
        """Replace only the later adjustment currency inside the second source line."""
        text = payload.decode("utf-8")
        line_marker = f"<Nb>{self.second_line_number}</Nb>"
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

        amount_text = format(self.second_line_later_adjustment_amount, "f")
        old_block = (
            "<AdjstmntAmtAndRsn>\n"
            f'                        <Amt Ccy="{old_currency_code}">{amount_text}</Amt>\n'
            "                        <CdtDbtInd>DBIT</CdtDbtInd>\n"
            "                        <Rsn>FEES</Rsn>\n"
            "                        <AddtlInf>Processing fee</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>"
        )
        new_block = (
            "<AdjstmntAmtAndRsn>\n"
            f'                        <Amt Ccy="{new_currency_code}">{amount_text}</Amt>\n'
            "                        <CdtDbtInd>DBIT</CdtDbtInd>\n"
            "                        <Rsn>FEES</Rsn>\n"
            "                        <AddtlInf>Processing fee</AddtlInf>\n"
            "                      </AdjstmntAmtAndRsn>"
        )
        if line_segment.count(old_block) != 1:
            raise AssertionError("target second-line later adjustment must occur exactly once")
        if line_segment.index(old_block) != second_adjustment_index:
            raise AssertionError("target adjustment must be the second source adjustment")
        if old_block != new_block and line_segment.count(new_block) != 0:
            raise AssertionError("replacement adjustment currency must not pre-exist")

        changed_line = line_segment.replace(old_block, new_block, 1)
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"structured-line-adjustment-currency-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
