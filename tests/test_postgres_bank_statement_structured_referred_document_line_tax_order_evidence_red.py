"""REDs for repeated line-tax source-order preservation."""

from __future__ import annotations

import hashlib
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
    test_postgres_bank_statement_structured_referred_document_line_tax_evidence_red
    as tax_contract,
)

_PARENT_TEST = tax_contract.BankStatementStructuredLineTaxEvidenceRedTests
_CORRECTION_ERROR = tax_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineTaxOrderEvidenceRedTests(unittest.TestCase):
    """Retain repeated TaxAmt members in exact source order."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL structured-remittance fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare one second line whose two complete tax members are source-swapped."""
        self.parent = _PARENT_TEST(
            "test_later_line_tax_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_payload = self.parent.base_payload
        self.base_statement = self.parent.base_statement
        self.base_projection = self.parent._structured_projection(
            self.parent.base_local_tax_amount
        )
        self._assert_tax_type_order(self.base_projection, ["STAT", "LOCL"])

        self.changed_payload = self._swap_second_line_taxes(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._reverse_second_line_taxes(self.base_projection)
        self._assert_tax_type_order(self.changed_projection, ["LOCL", "STAT"])

    def test_repeated_line_tax_source_order_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind an order-only tax change to raw and normalized evidence identity."""
        self.assertEqual(
            self._swap_second_line_taxes(self.changed_payload),
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

    def test_rejected_tax_order_preserves_complete_rls_scoped_statement_state(
        self,
    ) -> None:
        """Reject reordered taxes without relational or raw-artifact residue."""
        accepted = accept_bank_statement_evidence(
            self.parent._command(
                self.base_payload,
                "repeated-line-tax-order-base",
            ),
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            artifact_store=self.parent.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])

        before_rows = self._tenant_statement_rows()
        expected_artifacts = {
            self.base_statement.source_artifact_hash: self.base_payload,
        }
        self.assertEqual(self.parent.store._artifacts, expected_artifacts)
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
                self.parent._command(
                    self.changed_payload,
                    "repeated-line-tax-order-changed",
                ),
                posting.DATABASE_URL,
                self.parent.case.policy.tenant_reference,
                artifact_store=self.parent.store,
            )

        self.assertEqual(self._tenant_statement_rows(), before_rows)
        self.assertEqual(self.parent.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.parent.store._artifacts,
        )

    def test_buyer_read_retains_repeated_line_tax_source_order(self) -> None:
        """Persist changed-order hashes and return complete tax tuples in source order."""
        accepted = accept_bank_statement_evidence(
            self.parent._command(
                self.changed_payload,
                "repeated-line-tax-order-lookup",
            ),
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            artifact_store=self.parent.store,
        )
        record_id = str(accepted["bank_statement_record_id"])
        statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            record_id,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
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
        self._assert_tax_type_order(
            detail[_STRUCTURED_EVIDENCE_KEY],
            ["LOCL", "STAT"],
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

    def _swap_second_line_taxes(self, payload: bytes) -> bytes:
        """Swap exactly two complete TaxAmt blocks inside Stockitem2."""
        text = payload.decode("utf-8")
        line_marker = f"<Nb>{self.parent.second_line_number}</Nb>"
        if text.count(line_marker) != 1:
            raise AssertionError("second line number marker must occur exactly once")
        marker_index = text.index(line_marker)
        line_start = text.rfind("<LineDtls>", 0, marker_index)
        line_end_start = text.find("</LineDtls>", marker_index)
        if line_start < 0 or line_end_start < 0:
            raise AssertionError("second line boundaries must be present")
        line_end = line_end_start + len("</LineDtls>")
        line_segment = text[line_start:line_end]

        taxes = list(
            re.finditer(
                r"<TaxAmt>.*?</TaxAmt>",
                line_segment,
                re.DOTALL,
            )
        )
        if len(taxes) != 2:
            raise AssertionError("second source line must contain exactly two taxes")
        first = taxes[0].group(0)
        second = taxes[1].group(0)
        if {
            self._tax_identity(first),
            self._tax_identity(second),
        } != {"STAT", "LOCL"}:
            raise AssertionError(
                "second line must contain exactly the STAT and LOCL tax tuples"
            )

        between = line_segment[taxes[0].end() : taxes[1].start()]
        swapped_line = (
            line_segment[: taxes[0].start()]
            + second
            + between
            + first
            + line_segment[taxes[1].end() :]
        )
        return (text[:line_start] + swapped_line + text[line_end:]).encode("utf-8")

    @staticmethod
    def _tax_identity(block: str) -> str:
        """Identify only the two canonical complete second-line tax tuples."""
        stat_markers = (
            "<Tp><Cd>STAT</Cd></Tp>",
            '<Amt Ccy="KRW">950.00</Amt>',
        )
        local_markers = (
            "<Tp><Cd>LOCL</Cd></Tp>",
            '<Amt Ccy="KRW">50.00</Amt>',
        )
        stat = all(marker in block for marker in stat_markers)
        local = all(marker in block for marker in local_markers)
        if stat == local:
            raise AssertionError("tax block must match exactly one canonical tuple")
        return "STAT" if stat else "LOCL"

    @staticmethod
    def _reverse_second_line_taxes(
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Reverse the two complete second-line tax mappings only."""
        changed = deepcopy(projection)
        if len(changed) != 1:
            raise AssertionError("tax-order RED requires one referred document")
        lines = changed[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("tax-order RED requires exactly two source lines")
        taxes = lines[1].get("tax_amounts")
        if not isinstance(taxes, list) or len(taxes) != 2:
            raise AssertionError("second line must contain exactly two tax amounts")
        lines[1]["tax_amounts"] = [
            deepcopy(taxes[1]),
            deepcopy(taxes[0]),
        ]
        return changed

    @staticmethod
    def _assert_tax_type_order(
        projection: list[dict[str, object]],
        expected: list[str],
    ) -> None:
        """Assert exact repeated-tax order on the second source line."""
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("structured projection must contain two source lines")
        taxes = lines[1].get("tax_amounts")
        if not isinstance(taxes, list):
            raise AssertionError("second source line must retain tax evidence")
        actual = [str(tax["type_code"]) for tax in taxes]
        if actual != expected:
            raise AssertionError(
                f"tax source order mismatch: expected {expected!r}, got {actual!r}"
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
        tenant_reference = self.parent.case.policy.tenant_reference
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
