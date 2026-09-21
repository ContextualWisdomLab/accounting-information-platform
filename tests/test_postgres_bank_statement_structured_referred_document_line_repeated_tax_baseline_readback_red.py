"""RED coverage for persisted readback of the two-member line-tax baseline."""

from __future__ import annotations

import unittest
from copy import deepcopy
from decimal import Decimal

from accounting_information_platform import (
    accept_bank_statement_evidence,
    lookup_bank_statement,
    lookup_bank_statement_entries,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_repeated_tax_contraction_evidence_red
    as tax_contraction_contract,
)

_CONTRACT = (
    tax_contraction_contract.
    BankStatementStructuredLineRepeatedTaxContractionEvidenceRedTests
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_TAX_KEY = "tax_amounts"


class BankStatementStructuredLineRepeatedTaxBaselineReadbackRedTests(
    unittest.TestCase
):
    """Keep both source-ordered TaxAmt members through persistence and buyer readback."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-tax contraction fixture."""
        _CONTRACT.setUpClass()

    def setUp(self) -> None:
        """Create the exact two-tax baseline owned by the contraction contract."""
        self.contract = _CONTRACT(
            "test_later_tax_removal_is_material_to_each_evidence_hash"
        )
        self.contract.setUp()
        self.addCleanup(self.contract.doCleanups)

    def test_buyer_read_retains_two_member_tax_baseline_in_source_order(self) -> None:
        """Read back STAT then LOCL before testing the later 2-to-1 contraction."""
        accepted = accept_bank_statement_evidence(
            self.contract.line_contract._command(
                self.contract.base_payload,
                "line-tax-contraction-two-member-baseline",
            ),
            posting.DATABASE_URL,
            self.contract.line_contract.case.policy.tenant_reference,
            artifact_store=self.contract.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        self.assertEqual(
            self.contract.line_contract.store._artifacts,
            {
                self.contract.base_statement.source_artifact_hash:
                self.contract.base_payload
            },
        )

        record_id = str(accepted["bank_statement_record_id"])
        statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.contract.line_contract.case.policy.tenant_reference,
            record_id,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.contract.line_contract.case.policy.tenant_reference,
            record_id,
        )
        entries = document["bank_statement_entries"]
        self.assertEqual(len(entries), len(self.contract.base_statement.entries))

        entry = entries[0]
        detail = entry["entry_details"][0]
        expected_entry = self.contract.base_statement.entries[0]
        expected_detail = expected_entry.entry_details[0]

        self.assertEqual(
            statement["source_artifact_hash"],
            self.contract.base_statement.source_artifact_hash,
        )
        self.assertEqual(
            statement["normalized_payload_hash"],
            self.contract.base_statement.normalized_payload_hash,
        )
        self.assertEqual(entry["source_entry_hash"], expected_entry.source_entry_hash)
        self.assertEqual(detail["source_detail_hash"], expected_detail.source_detail_hash)
        self.assertEqual(
            detail[_STRUCTURED_EVIDENCE_KEY],
            self.contract.base_projection,
        )

        first_line, second_line = self.contract.parent._line_details(
            detail[_STRUCTURED_EVIDENCE_KEY]
        )
        expected_taxes = [
            deepcopy(tax_contraction_contract._STAT),
            deepcopy(tax_contraction_contract._LOCL),
        ]
        self.assertEqual(second_line.get(_TAX_KEY), expected_taxes)

        stable_second_line = deepcopy(second_line)
        stable_second_line.pop(_TAX_KEY)
        self.assertEqual(
            stable_second_line,
            self.contract.second_line_without_taxes,
        )
        self.assertEqual(first_line, self.contract.first_line_projection)
        self.assertEqual(
            second_line["discount_applied_amounts"],
            [{"type_code": "APDS", "amount": "100", "currency_code": "KRW"}],
        )
        self.assertNotIn("due_payable_amount", second_line)
        self.assertNotIn("remitted_amount", second_line)
        self.assertNotIn("description", second_line)

        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

        sibling_entry = entries[1]
        expected_sibling = self.contract.base_statement.entries[1]
        self.assertEqual(
            sibling_entry["source_entry_hash"],
            expected_sibling.source_entry_hash,
        )
        self.assertEqual(
            Decimal(str(sibling_entry["entry_amount"])),
            expected_sibling.entry_amount,
        )
        self.assertEqual(
            sibling_entry["entry_currency_code"],
            expected_sibling.entry_currency_code,
        )
        self.assertEqual(
            len(sibling_entry["entry_details"]),
            len(expected_sibling.entry_details),
        )
        for persisted_detail, source_detail in zip(
            sibling_entry["entry_details"],
            expected_sibling.entry_details,
            strict=True,
        ):
            self.assertEqual(
                persisted_detail["source_detail_hash"],
                source_detail.source_detail_hash,
            )
            self.assertEqual(
                Decimal(str(persisted_detail["detail_amount"])),
                source_detail.detail_amount,
            )
            self.assertEqual(
                persisted_detail["detail_currency_code"],
                source_detail.detail_currency_code,
            )


if __name__ == "__main__":
    unittest.main()
