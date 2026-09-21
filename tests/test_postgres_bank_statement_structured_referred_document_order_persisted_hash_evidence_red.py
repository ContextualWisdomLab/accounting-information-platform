"""RED for persisted hash binding of referred-document source order."""

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
    test_postgres_bank_statement_structured_referred_document_order_evidence_red
    as order_contract,
)

_PARENT_TEST = (
    order_contract.BankStatementStructuredReferredDocumentOrderEvidenceRedTests
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredReferredDocumentOrderPersistedHashEvidenceRedTests(
    unittest.TestCase
):
    """Bind persisted buyer hashes to the successful reordered source."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Reuse the exact complete-document reorder fixture from the parent RED."""
        self.parent = _PARENT_TEST(
            "test_buyer_read_retains_referred_document_source_order"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

    def test_reordered_buyer_read_binds_persisted_hashes_to_changed_source(self) -> None:
        """Persist changed-order statement, entry, and detail hashes exactly."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.parent.changed_payload,
                "referred-document-order-persisted-hash-lookup",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
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
        changed_entry = self.parent.changed_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]

        self.assertEqual(
            statement["source_artifact_hash"],
            self.parent.changed_statement.source_artifact_hash,
        )
        self.assertEqual(
            statement["normalized_payload_hash"],
            self.parent.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(entry["source_entry_hash"], changed_entry.source_entry_hash)
        self.assertEqual(
            detail["source_detail_hash"],
            changed_detail.source_detail_hash,
        )
        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.parent.changed_projection,
        )
        self.assertEqual(
            [
                item["document_number"]
                for item in detail[_STRUCTURED_EVIDENCE_KEY]
            ],
            [
                self.parent.second_document_number,
                self.parent.first_document_number,
            ],
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


if __name__ == "__main__":
    unittest.main()
