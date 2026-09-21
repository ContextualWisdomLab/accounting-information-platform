"""RED for persisted readback of the seeded repeated-discount baseline."""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform import (
    accept_bank_statement_evidence,
    lookup_bank_statement,
    lookup_bank_statement_entries,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_repeated_discount_contraction_evidence_red
    as contraction_contract,
)

_PARENT_TEST = (
    contraction_contract.
    BankStatementStructuredLineRepeatedDiscountContractionEvidenceRedTests
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_APDS = {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
_STDS = {"type_code": "STDS", "amount": "50", "currency_code": "KRW"}


class BankStatementRepeatedDiscountContractionPersistenceEvidenceRedTests(
    unittest.TestCase
):
    """Prove the accepted APDS/STDS baseline survives persistence and readback."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL contraction fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Build the exact seeded two-discount baseline owned by the parent RED."""
        self.parent = _PARENT_TEST(
            "test_later_discount_removal_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)

    def test_seeded_discount_population_survives_persistence_and_readback(self) -> None:
        """Reject persistence that truncates APDS/STDS to its first member."""
        accepted = accept_bank_statement_evidence(
            self.parent.line_contract._command(
                self.parent.base_payload,
                "line-discount-contraction-seeded-readback",
            ),
            posting.DATABASE_URL,
            self.parent.line_contract.case.policy.tenant_reference,
            artifact_store=self.parent.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.parent.line_contract.case.policy.tenant_reference,
            record_id,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.parent.line_contract.case.policy.tenant_reference,
            record_id,
        )
        entries = document["bank_statement_entries"]
        self.assertEqual(len(entries), len(self.parent.base_statement.entries))
        entry = entries[0]
        detail = entry["entry_details"][0]
        expected_entry = self.parent.base_statement.entries[0]
        expected_detail = expected_entry.entry_details[0]

        self.assertEqual(
            statement["source_artifact_hash"],
            self.parent.base_statement.source_artifact_hash,
        )
        self.assertEqual(
            statement["normalized_payload_hash"],
            self.parent.base_statement.normalized_payload_hash,
        )
        self.assertEqual(entry["source_entry_hash"], expected_entry.source_entry_hash)
        self.assertEqual(detail["source_detail_hash"], expected_detail.source_detail_hash)
        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.parent.base_projection,
        )

        first_line, second_line = self.parent.parent.parent.parent._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        expected_first, expected_second = self.parent.parent.parent.parent._line_details(
            self.parent.base_projection
        )
        self.assertEqual(first_line, expected_first)
        self.assertEqual(second_line, expected_second)
        self.assertEqual(second_line.get("discount_applied_amounts"), [_APDS, _STDS])
        self.assertNotIn("due_payable_amount", second_line)
        self.assertNotIn("remitted_amount", second_line)
        self.assertNotIn("description", second_line)
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")
        self.assertEqual(
            self.parent.line_contract.store._artifacts,
            {self.parent.base_statement.source_artifact_hash: self.parent.base_payload},
        )


if __name__ == "__main__":
    unittest.main()
