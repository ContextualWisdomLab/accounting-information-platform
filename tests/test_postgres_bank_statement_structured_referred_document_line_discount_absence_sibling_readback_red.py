"""REDs for complete sibling readback during line discount absence changes."""

from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from accounting_information_platform import accept_bank_statement_evidence
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_discount_absence_evidence_red
    as discount_absence_contract,
)

_CONTRACT = (
    discount_absence_contract.
    BankStatementStructuredLineDiscountAbsenceEvidenceRedTests
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_ENTRY_KEYS = {
    "bank_statement_entry_id",
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
    "source_entry_hash",
    "entry_details",
}
_DETAIL_KEYS = {
    "detail_sequence_number",
    "source_locator_path",
    "detail_amount",
    "detail_currency_code",
    "credit_debit_code",
    "end_to_end_reference",
    "remittance_evidence_text",
    "source_detail_hash",
}


class BankStatementStructuredLineDiscountAbsenceSiblingReadbackRedTests(
    unittest.TestCase
):
    """Compare every buyer-visible sibling field before and after APDS absence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the exact real-PostgreSQL discount-absence fixture."""
        _CONTRACT.setUpClass()

    def setUp(self) -> None:
        """Create an isolated discount-absence contract instance."""
        self.contract = _CONTRACT(
            "test_discount_present_baseline_reads_back_before_absence"
        )
        self.contract.setUp()
        self.addCleanup(self.contract.doCleanups)

    def test_discount_present_baseline_reads_complete_sibling_projection(self) -> None:
        """Prevent APDS-present acceptance from masking sibling readback loss."""
        self._accept_and_assert_complete_sibling(
            self.contract.base_payload,
            self.contract.base_statement,
            self.contract.base_projection,
            "line-discount-complete-sibling-base",
            expect_second_discount=True,
        )

    def test_discount_absence_reads_complete_sibling_projection(self) -> None:
        """Keep every buyer-visible sibling field exact after APDS becomes absent."""
        self._accept_and_assert_complete_sibling(
            self.contract.changed_payload,
            self.contract.changed_statement,
            self.contract.changed_projection,
            "line-discount-complete-sibling-changed",
            expect_second_discount=False,
        )

    def _accept_and_assert_complete_sibling(
        self,
        payload: bytes,
        statement: object,
        projection: list[dict[str, object]],
        idempotency_key: str,
        *,
        expect_second_discount: bool,
    ) -> None:
        """Persist one source and compare the complete public sibling document."""
        accepted = accept_bank_statement_evidence(
            self.contract.line_contract._command(payload, idempotency_key),
            posting.DATABASE_URL,
            self.contract.line_contract.case.policy.tenant_reference,
            artifact_store=self.contract.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        statement_document, entries = self.contract.parent._lookup(
            str(accepted["bank_statement_record_id"]),
            statement,
        )
        self.contract.parent._assert_statement_hashes(
            statement_document,
            statement,
        )
        self.contract._assert_primary_entry(
            entries[0],
            statement.entries[0],
            projection,
            expect_second_discount=expect_second_discount,
        )
        self._assert_complete_sibling(entries[1], statement.entries[1])

    def _assert_complete_sibling(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
    ) -> None:
        """Compare every field the public entry/detail read API serializes."""
        self.assertEqual(set(persisted_entry), _ENTRY_KEYS)
        UUID(str(persisted_entry["bank_statement_entry_id"]))
        self.assertEqual(
            persisted_entry["source_entry_identity"],
            expected_entry.source_entry_identity,
        )
        self.assertEqual(
            persisted_entry["entry_sequence_number"],
            expected_entry.entry_sequence_number,
        )
        self.assertEqual(
            persisted_entry["source_locator_path"],
            expected_entry.source_locator_path,
        )
        self._assert_timestamp(
            persisted_entry["booking_occurred_at"],
            expected_entry.booking_occurred_at,
        )
        self._assert_timestamp(
            persisted_entry["value_occurred_at"],
            expected_entry.value_occurred_at,
        )
        self.assertEqual(
            Decimal(str(persisted_entry["entry_amount"])),
            expected_entry.entry_amount,
        )
        for field in (
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
            "source_entry_hash",
        ):
            self.assertEqual(
                persisted_entry[field],
                getattr(expected_entry, field),
            )

        persisted_details = persisted_entry["entry_details"]
        expected_details = expected_entry.entry_details
        self.assertEqual(len(persisted_details), len(expected_details))
        for persisted_detail, expected_detail in zip(
            persisted_details,
            expected_details,
            strict=True,
        ):
            self.assertEqual(set(persisted_detail), _DETAIL_KEYS)
            self.assertEqual(
                persisted_detail["detail_sequence_number"],
                expected_detail.detail_sequence_number,
            )
            self.assertEqual(
                persisted_detail["source_locator_path"],
                expected_detail.source_locator_path,
            )
            self.assertEqual(
                Decimal(str(persisted_detail["detail_amount"])),
                expected_detail.detail_amount,
            )
            for field in (
                "detail_currency_code",
                "credit_debit_code",
                "end_to_end_reference",
                "remittance_evidence_text",
                "source_detail_hash",
            ):
                self.assertEqual(
                    persisted_detail[field],
                    getattr(expected_detail, field),
                )
            self.assertNotIn(_STRUCTURED_EVIDENCE_KEY, persisted_detail)

    def _assert_timestamp(
        self,
        persisted: object,
        expected: datetime | None,
    ) -> None:
        """Compare the read API timestamp string to the normalized datetime."""
        if expected is None:
            self.assertIsNone(persisted)
            return
        self.assertIsInstance(persisted, str)
        parsed = datetime.fromisoformat(str(persisted).replace("Z", "+00:00"))
        self.assertEqual(parsed, expected)


if __name__ == "__main__":
    unittest.main()
