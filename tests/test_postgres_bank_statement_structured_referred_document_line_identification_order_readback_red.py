"""RED for non-lexicographic readback of repeated referred-document line Id order."""

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
    test_postgres_bank_statement_structured_referred_document_line_identification_order_evidence_red
    as order_contract,
)

_PARENT_TEST = order_contract.BankStatementStructuredLineIdentificationOrderEvidenceRedTests
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineIdentificationOrderReadbackRedTests(unittest.TestCase):
    """Prevent persistence/readback sorting after source-order-aware normalization."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL repeated-identification order fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare the parent's non-lexicographic SKNB-then-PRNB source order."""
        self.parent = _PARENT_TEST(
            "test_repeated_line_identification_order_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)

    def test_buyer_read_retains_base_sknb_then_prnb_source_order(self) -> None:
        """Round-trip the source order that an ascending type/number sort would reverse."""
        accepted = accept_bank_statement_evidence(
            self.parent.line_contract._command(
                self.parent.base_payload,
                "line-identification-order-base-readback",
            ),
            posting.DATABASE_URL,
            self.parent.line_contract.case.policy.tenant_reference,
            artifact_store=self.parent.line_contract.store,
        )
        self.assertEqual(
            self.parent.line_contract.store._artifacts,
            {self.parent.base_statement.source_artifact_hash: self.parent.base_payload},
        )
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
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]
        base_entry = self.parent.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertEqual(
            statement["source_artifact_hash"],
            self.parent.base_statement.source_artifact_hash,
        )
        self.assertEqual(
            statement["normalized_payload_hash"],
            self.parent.base_statement.normalized_payload_hash,
        )
        self.assertEqual(entry["source_entry_hash"], base_entry.source_entry_hash)
        self.assertEqual(detail["source_detail_hash"], base_detail.source_detail_hash)
        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self.parent.base_projection,
        )

        second_line = detail[_STRUCTURED_EVIDENCE_KEY][0]["line_details"][1]
        self.assertEqual(second_line["line_type_code"], "SKNB")
        self.assertEqual(
            second_line["line_number"],
            self.parent.line_contract.second_line_number,
        )
        self.assertEqual(
            second_line["related_date"],
            self.parent.line_contract.line_related_date,
        )
        self.assertEqual(
            second_line["line_identifications"],
            [
                {
                    "type_code": "SKNB",
                    "number": self.parent.line_contract.second_line_number,
                    "related_date": self.parent.line_contract.line_related_date,
                },
                {
                    "type_code": self.parent.parent.second_identification_type_code,
                    "number": self.parent.parent.second_identification_number,
                    "related_date": self.parent.parent.second_identification_related_date,
                },
            ],
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")


if __name__ == "__main__":
    unittest.main()
