"""REDs for repeated line-adjustment source-order preservation."""

from __future__ import annotations

import re
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
    test_postgres_bank_statement_structured_referred_document_line_adjustment_additional_information_evidence_red
    as adjustment_contract,
)

_PARENT_TEST = (
    adjustment_contract.
    BankStatementStructuredLineAdjustmentAdditionalInformationEvidenceRedTests
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineAdjustmentOrderEvidenceRedTests(unittest.TestCase):
    """Retain repeated AdjstmntAmtAndRsn members in exact source order."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL structured-remittance fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare one second line whose two complete adjustments are source-swapped."""
        self.parent = _PARENT_TEST(
            "test_line_adjustment_additional_information_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.credit_debit_contract.line_contract

        self.base_payload = self.parent.base_payload
        self.base_statement = self.parent.base_statement
        self.base_projection = self.parent._structured_projection(
            self.parent.base_additional_information
        )
        self._assert_adjustment_reasons(self.base_projection, ["ADJT", "FEES"])

        self.changed_payload = self._swap_second_line_adjustments(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._reverse_second_line_adjustments(
            self.base_projection
        )
        self._assert_adjustment_reasons(self.changed_projection, ["FEES", "ADJT"])

    def test_repeated_line_adjustment_source_order_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind an order-only adjustment change to raw and normalized evidence identity."""
        self.assertEqual(
            self._swap_second_line_adjustments(self.changed_payload),
            self.base_payload,
        )
        self._assert_non_structured_normalization_unchanged()

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        for value in (
            self.base_statement.source_artifact_hash,
            self.changed_statement.source_artifact_hash,
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
            self.base_statement.source_artifact_hash,
            self.changed_statement.source_artifact_hash,
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

    def test_rejected_adjustment_order_preserves_complete_tenant_statement_state(
        self,
    ) -> None:
        """Reject reordered adjustments without relational or raw-artifact residue."""
        accepted = accept_bank_statement_evidence(
            self.parent.credit_debit_contract._command(
                self.base_payload,
                "repeated-line-adjustment-order-base",
            ),
            posting.DATABASE_URL,
            self.parent.credit_debit_contract.case.policy.tenant_reference,
            artifact_store=self.parent.credit_debit_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])

        before_rows = self._tenant_statement_rows()
        before_artifacts = dict(self.parent.credit_debit_contract.store._artifacts)
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
                self.parent.credit_debit_contract._command(
                    self.changed_payload,
                    "repeated-line-adjustment-order-changed",
                ),
                posting.DATABASE_URL,
                self.parent.credit_debit_contract.case.policy.tenant_reference,
                artifact_store=self.parent.credit_debit_contract.store,
            )

        self.assertEqual(self._tenant_statement_rows(), before_rows)
        self.assertEqual(
            self.parent.credit_debit_contract.store._artifacts,
            before_artifacts,
        )

    def test_buyer_read_retains_repeated_line_adjustment_source_order(self) -> None:
        """Persist changed-order hashes and return adjustment tuples in source order."""
        accepted = accept_bank_statement_evidence(
            self.parent.credit_debit_contract._command(
                self.changed_payload,
                "repeated-line-adjustment-order-lookup",
            ),
            posting.DATABASE_URL,
            self.parent.credit_debit_contract.case.policy.tenant_reference,
            artifact_store=self.parent.credit_debit_contract.store,
        )
        record_id = str(accepted["bank_statement_record_id"])
        statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.parent.credit_debit_contract.case.policy.tenant_reference,
            record_id,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.parent.credit_debit_contract.case.policy.tenant_reference,
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
        self._assert_adjustment_reasons(
            detail[_STRUCTURED_EVIDENCE_KEY],
            ["FEES", "ADJT"],
        )
        self._assert_exact_persisted_transaction_amount(entry, detail)

    def _swap_second_line_adjustments(self, payload: bytes) -> bytes:
        """Swap exactly two complete adjustment blocks inside Stockitem2."""
        text = payload.decode("utf-8")
        line_marker = (
            f"<Nb>{self.parent.credit_debit_contract.second_line_number}</Nb>"
        )
        if text.count(line_marker) != 1:
            raise AssertionError("second line number marker must occur exactly once")
        marker_index = text.index(line_marker)
        line_start = text.rfind("<LineDtls>", 0, marker_index)
        line_end_start = text.find("</LineDtls>", marker_index)
        if line_start < 0 or line_end_start < 0:
            raise AssertionError("second line boundaries must be present")
        line_end = line_end_start + len("</LineDtls>")
        line_segment = text[line_start:line_end]

        adjustments = list(
            re.finditer(
                r"<AdjstmntAmtAndRsn>.*?</AdjstmntAmtAndRsn>",
                line_segment,
                re.DOTALL,
            )
        )
        if len(adjustments) != 2:
            raise AssertionError("second source line must contain exactly two adjustments")
        first = adjustments[0].group(0)
        second = adjustments[1].group(0)
        if {
            self._adjustment_identity(first),
            self._adjustment_identity(second),
        } != {"ADJT", "FEES"}:
            raise AssertionError(
                "second line must contain exactly the ADJT and FEES adjustment tuples"
            )

        between = line_segment[adjustments[0].end() : adjustments[1].start()]
        swapped_line = (
            line_segment[: adjustments[0].start()]
            + second
            + between
            + first
            + line_segment[adjustments[1].end() :]
        )
        return (text[:line_start] + swapped_line + text[line_end:]).encode("utf-8")

    @staticmethod
    def _adjustment_identity(block: str) -> str:
        """Identify only the two canonical complete adjustment tuples."""
        adjt_markers = (
            '<Amt Ccy="KRW">200.00</Amt>',
            "<CdtDbtInd>CRDT</CdtDbtInd>",
            "<Rsn>ADJT</Rsn>",
            "<AddtlInf>Contract true-up</AddtlInf>",
        )
        fees_markers = (
            '<Amt Ccy="KRW">50.00</Amt>',
            "<CdtDbtInd>DBIT</CdtDbtInd>",
            "<Rsn>FEES</Rsn>",
            "<AddtlInf>Processing fee</AddtlInf>",
        )
        adjt = all(marker in block for marker in adjt_markers)
        fees = all(marker in block for marker in fees_markers)
        if adjt == fees:
            raise AssertionError("adjustment block must match exactly one canonical tuple")
        return "ADJT" if adjt else "FEES"

    @staticmethod
    def _reverse_second_line_adjustments(
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Reverse the two complete second-line adjustment mappings only."""
        changed = deepcopy(projection)
        if len(changed) != 1:
            raise AssertionError("adjustment-order RED requires one referred document")
        lines = changed[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("adjustment-order RED requires exactly two source lines")
        adjustments = lines[1].get("adjustments")
        if not isinstance(adjustments, list) or len(adjustments) != 2:
            raise AssertionError("second line must contain exactly two adjustments")
        lines[1]["adjustments"] = [
            deepcopy(adjustments[1]),
            deepcopy(adjustments[0]),
        ]
        return changed

    @staticmethod
    def _assert_adjustment_reasons(
        projection: list[dict[str, object]],
        expected: list[str],
    ) -> None:
        """Assert exact repeated-adjustment order on the second source line."""
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("structured projection must contain two source lines")
        adjustments = lines[1].get("adjustments")
        if not isinstance(adjustments, list):
            raise AssertionError("second source line must retain adjustment evidence")
        actual = [str(adjustment["reason_code"]) for adjustment in adjustments]
        if actual != expected:
            raise AssertionError(
                f"adjustment source order mismatch: expected {expected!r}, got {actual!r}"
            )

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove order-only change cannot mask collateral normalized-field drift."""
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

    def _tenant_statement_rows(self) -> dict[str, tuple[tuple[object, ...], ...]]:
        """Snapshot all columns in the tenant-scoped statement-evidence population."""
        tenant_reference = self.parent.credit_debit_contract.case.policy.tenant_reference
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

            tables = (
                ("bank_statement_artifact", "bank_statement_artifact_id"),
                ("bank_statement_record", "bank_statement_record_id"),
                ("bank_statement_entry", "bank_statement_entry_id"),
                ("bank_statement_entry_detail", "bank_statement_entry_detail_id"),
            )
            snapshots: dict[str, tuple[tuple[object, ...], ...]] = {}
            for table_name, primary_key in tables:
                rows = connection.execute(
                    f"""
                    SELECT *
                    FROM accounting_integration.{table_name}
                    WHERE tenant_account_id = %s
                    ORDER BY {primary_key}
                    """,
                    (tenant_id,),
                ).fetchall()
                snapshots[table_name] = tuple(tuple(row) for row in rows)
        return snapshots

    @staticmethod
    def _assert_exact_persisted_transaction_amount(
        entry: dict[str, object],
        detail: dict[str, object],
    ) -> None:
        """Keep adjustment source order separate from transaction amount truth."""
        if Decimal(str(entry["entry_amount"])) != Decimal("25000.00"):
            raise AssertionError("entry transaction amount must remain exactly 25000.00")
        if entry["entry_currency_code"] != "KRW":
            raise AssertionError("entry transaction currency must remain KRW")
        if Decimal(str(detail["detail_amount"])) != Decimal("25000.00"):
            raise AssertionError("detail transaction amount must remain exactly 25000.00")
        if detail["detail_currency_code"] != "KRW":
            raise AssertionError("detail transaction currency must remain KRW")


if __name__ == "__main__":
    unittest.main()
