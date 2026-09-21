"""REDs for repeated line-discount source-order preservation."""

from __future__ import annotations

import re
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
    test_postgres_bank_statement_structured_referred_document_line_repeated_discount_evidence_red
    as repeated_discount_contract,
)

_PARENT_TEST = (
    repeated_discount_contract.
    BankStatementStructuredRepeatedLineDiscountEvidenceRedTests
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineDiscountOrderEvidenceRedTests(unittest.TestCase):
    """Retain repeated DscntApldAmt members in exact source order."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare one second line whose two complete discounts are source-swapped."""
        self.parent = _PARENT_TEST(
            "test_later_repeated_line_discount_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_payload = self.parent.base_payload
        self.base_statement = self.parent.base_statement
        self.base_projection = self.parent._structured_projection(
            self.parent.base_repeated_discount_amount
        )
        self._assert_discount_types(self.base_projection, ["APDS", "STDS"])

        self.changed_payload = self._swap_second_line_discounts(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = self._reverse_second_line_discounts(
            self.base_projection
        )
        self._assert_discount_types(self.changed_projection, ["STDS", "APDS"])

    def test_repeated_line_discount_source_order_is_material_to_each_evidence_hash(
        self,
    ) -> None:
        """Bind an order-only repeated-discount change to canonical evidence identity."""
        self.assertEqual(
            self._swap_second_line_discounts(self.changed_payload),
            self.base_payload,
        )
        self._assert_non_structured_normalization_unchanged()

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

    def test_changed_discount_order_fails_closed_without_mutating_accepted_evidence(
        self,
    ) -> None:
        """Reject reordered discounts without mutating relational or raw evidence."""
        accepted = accept_bank_statement_evidence(
            self.parent._command(
                self.base_payload,
                "repeated-line-discount-order-base",
            ),
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            artifact_store=self.parent.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        before_statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            record_id,
        )
        before_entries = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            record_id,
        )
        before_artifacts = dict(self.parent.store._artifacts)

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.parent._command(
                    self.changed_payload,
                    "repeated-line-discount-order-changed",
                ),
                posting.DATABASE_URL,
                self.parent.case.policy.tenant_reference,
                artifact_store=self.parent.store,
            )

        after_statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            record_id,
        )
        after_entries = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.parent.case.policy.tenant_reference,
            record_id,
        )
        self.assertEqual(self.parent.store._artifacts, before_artifacts)
        self.assertEqual(after_statement, before_statement)
        self.assertEqual(after_entries, before_entries)

        persisted_entry = after_entries["bank_statement_entries"][0]
        persisted_detail = persisted_entry["entry_details"][0]
        self.assertEqual(
            persisted_detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.base_projection,
        )
        self._assert_discount_types(
            persisted_detail[_STRUCTURED_EVIDENCE_KEY],
            ["APDS", "STDS"],
        )
        self._assert_exact_persisted_transaction_amount(
            persisted_entry,
            persisted_detail,
        )

    def test_buyer_read_retains_repeated_line_discount_source_order(self) -> None:
        """Persist changed-order hashes and return discounts in exact source order."""
        accepted = accept_bank_statement_evidence(
            self.parent._command(
                self.changed_payload,
                "repeated-line-discount-order-lookup",
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
        self.assertEqual(entry["source_entry_hash"], changed_entry.source_entry_hash)
        self.assertEqual(detail["source_detail_hash"], changed_detail.source_detail_hash)
        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.changed_projection,
        )
        self._assert_discount_types(
            detail[_STRUCTURED_EVIDENCE_KEY],
            ["STDS", "APDS"],
        )
        self._assert_exact_persisted_transaction_amount(entry, detail)

    def _swap_second_line_discounts(self, payload: bytes) -> bytes:
        """Swap exactly two complete discount blocks inside Stockitem2."""
        text = payload.decode("utf-8")
        line_start, line_end, line_segment = self.parent._second_line_segment(text)
        discounts = list(
            re.finditer(
                r"<DscntApldAmt>.*?</DscntApldAmt>",
                line_segment,
                re.DOTALL,
            )
        )
        if len(discounts) != 2:
            raise AssertionError("second source line must contain exactly two discounts")
        first = discounts[0].group(0)
        second = discounts[1].group(0)
        first_apds = "<Cd>APDS</Cd>" in first
        first_stds = "<Cd>STDS</Cd>" in first
        second_apds = "<Cd>APDS</Cd>" in second
        second_stds = "<Cd>STDS</Cd>" in second
        if (
            first_apds == first_stds
            or second_apds == second_stds
            or first_apds == second_apds
        ):
            raise AssertionError("source discounts must contain one APDS and one STDS")
        between = line_segment[discounts[0].end() : discounts[1].start()]
        swapped_line = (
            line_segment[: discounts[0].start()]
            + second
            + between
            + first
            + line_segment[discounts[1].end() :]
        )
        return (text[:line_start] + swapped_line + text[line_end:]).encode("utf-8")

    @staticmethod
    def _reverse_second_line_discounts(
        projection: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Reverse the two complete second-line discount mappings only."""
        changed = deepcopy(projection)
        if len(changed) != 1:
            raise AssertionError("discount-order RED requires one referred document")
        lines = changed[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("discount-order RED requires exactly two source lines")
        discounts = lines[1].get("discount_applied_amounts")
        if not isinstance(discounts, list) or len(discounts) != 2:
            raise AssertionError("second line must contain exactly two discounts")
        lines[1]["discount_applied_amounts"] = [
            deepcopy(discounts[1]),
            deepcopy(discounts[0]),
        ]
        return changed

    @staticmethod
    def _assert_discount_types(
        projection: list[dict[str, object]],
        expected: list[str],
    ) -> None:
        """Assert exact repeated-discount order on the second source line."""
        lines = projection[0].get("line_details")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("structured projection must contain two source lines")
        discounts = lines[1].get("discount_applied_amounts")
        if not isinstance(discounts, list):
            raise AssertionError("second source line must retain discount evidence")
        actual = [str(discount["type_code"]) for discount in discounts]
        if actual != expected:
            raise AssertionError(
                f"discount source order mismatch: expected {expected!r}, got {actual!r}"
            )

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove discount-order-only change cannot mask scalar normalization drift."""
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

    @staticmethod
    def _assert_exact_persisted_transaction_amount(
        entry: dict[str, object],
        detail: dict[str, object],
    ) -> None:
        """Keep discount source order separate from authoritative transaction truth."""
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
