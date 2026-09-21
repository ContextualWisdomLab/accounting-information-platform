"""REDs for complete sibling readback during line adjustment absence changes."""

from __future__ import annotations

import unittest

from tests import (
    test_postgres_bank_statement_structured_referred_document_line_adjustment_absence_evidence_red
    as adjustment_absence_contract,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_discount_absence_sibling_readback_red
    as sibling_readback_contract,
)


class BankStatementStructuredLineAdjustmentAbsenceSiblingReadbackRedTests(
    unittest.TestCase
):
    """Compare every buyer-visible sibling field across adjustment absence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the exact real-PostgreSQL adjustment-absence fixture."""
        contract_type = (
            adjustment_absence_contract.
            BankStatementStructuredLineAdjustmentAbsenceEvidenceRedTests
        )
        contract_type.setUpClass()

    def setUp(self) -> None:
        """Create an isolated adjustment-absence contract instance."""
        contract_type = (
            adjustment_absence_contract.
            BankStatementStructuredLineAdjustmentAbsenceEvidenceRedTests
        )
        self.contract = contract_type(
            "test_adjustment_present_baseline_reads_back_before_zero_population"
        )
        self.contract.setUp()
        self.addCleanup(self.contract.doCleanups)

    def test_adjustment_present_baseline_reads_complete_sibling_projection(self) -> None:
        """Prevent ADJT-present acceptance from masking sibling readback loss."""
        self._persist_and_assert_complete_sibling(
            self.contract.base_payload,
            self.contract.base_statement,
            self.contract.base_projection,
            "line-adjustment-complete-sibling-base",
            expect_adjustment=True,
        )

    def test_adjustment_absence_reads_complete_sibling_projection(self) -> None:
        """Keep every buyer-visible sibling field exact after ADJT becomes absent."""
        self._persist_and_assert_complete_sibling(
            self.contract.changed_payload,
            self.contract.changed_statement,
            self.contract.changed_projection,
            "line-adjustment-complete-sibling-changed",
            expect_adjustment=False,
        )

    def _persist_and_assert_complete_sibling(
        self,
        payload: bytes,
        expected_statement: object,
        expected_projection: list[dict[str, object]],
        idempotency_key: str,
        *,
        expect_adjustment: bool,
    ) -> None:
        """Persist one source and apply the shared complete-sibling oracle."""
        statement, entries = self.contract._persist_and_read(
            payload,
            expected_statement,
            idempotency_key,
        )
        self.contract._assert_statement_hashes(statement, expected_statement)
        self.contract._assert_primary_entry(
            entries[0],
            expected_statement.entries[0],
            expected_projection,
            expect_adjustment=expect_adjustment,
        )
        helper_type = (
            sibling_readback_contract.
            BankStatementStructuredLineDiscountAbsenceSiblingReadbackRedTests
        )
        helper = helper_type(
            "test_discount_present_baseline_reads_complete_sibling_projection"
        )
        helper._assert_complete_sibling(
            entries[1],
            expected_statement.entries[1],
        )


if __name__ == "__main__":
    unittest.main()
