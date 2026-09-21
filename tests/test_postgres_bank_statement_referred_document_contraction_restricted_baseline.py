"""Restricted-runtime baseline regression for referred-document contraction."""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform import (
    accept_bank_statement_evidence,
    lookup_bank_statement,
    lookup_bank_statement_entries,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_contraction_evidence_red
    as contraction_contract,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_identification_cardinality_evidence_red
    as rls_snapshot_contract,
)

_CONTRACT = (
    contraction_contract.BankStatementStructuredReferredDocumentContractionEvidenceRedTests
)
_RLS_OWNER = (
    rls_snapshot_contract.
    BankStatementStructuredLineIdentificationCardinalityEvidenceRedTests
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementReferredDocumentContractionRestrictedBaselineTests(
    unittest.TestCase
):
    """Prove the tenant-bound baseline is complete before contraction rejection."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the exact real-PostgreSQL referred-document contraction fixture."""
        _CONTRACT.setUpClass()

    def setUp(self) -> None:
        """Build the current contraction fixture without changing production authority."""
        self.contract = _CONTRACT(
            "test_rejected_document_contraction_leaves_no_evidence_residue"
        )
        self.contract.setUp()
        self.addCleanup(self.contract.doCleanups)

    def test_restricted_runtime_reads_complete_two_document_baseline(self) -> None:
        """Bind the accepted RLS baseline to exact entries, details, hashes, and order."""
        runtime_url = _RLS_OWNER._restricted_bank_statement_runtime_url(self.contract)
        accepted = accept_bank_statement_evidence(
            self.contract.line_contract._command(
                self.contract.base_payload,
                "referred-document-contraction-restricted-baseline",
            ),
            runtime_url,
            self.contract.line_contract.case.policy.tenant_reference,
            artifact_store=self.contract.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        tenant_reference = self.contract.line_contract.case.policy.tenant_reference

        statement = lookup_bank_statement(
            runtime_url,
            tenant_reference,
            record_id,
        )
        document = lookup_bank_statement_entries(
            runtime_url,
            tenant_reference,
            record_id,
        )
        entries = document["bank_statement_entries"]
        self.assertEqual(len(entries), len(self.contract.base_statement.entries))

        self.contract._assert_statement_hashes(
            statement,
            self.contract.base_statement,
        )
        self.contract._assert_primary_entry(
            entries[0],
            self.contract.base_statement.entries[0],
            self.contract.base_projection,
            expected_document_numbers=(
                self.contract.first_document_number,
                self.contract.second_document_number,
            ),
        )
        self._assert_every_primary_detail(entries[0])
        self.contract._assert_sibling(
            entries[1],
            self.contract.base_statement.entries[1],
        )
        self.assertEqual(
            self.contract.line_contract.store._artifacts,
            {
                self.contract.base_statement.source_artifact_hash:
                self.contract.base_payload,
            },
        )

        before_rows = _RLS_OWNER._tenant_statement_rows(self.contract, runtime_url)
        self.assertTrue(before_rows["bank_statement_artifact"])
        self.assertTrue(before_rows["bank_statement_entry"])
        self.assertTrue(before_rows["bank_statement_entry_detail"])
        self.assertTrue(
            any(
                str(row[0]) == record_id
                for row in before_rows["bank_statement_record"]
            )
        )

    def _assert_every_primary_detail(self, persisted_entry: dict[str, object]) -> None:
        """Require complete detail hashes/amounts while keeping structure on detail one."""
        persisted_details = persisted_entry["entry_details"]
        expected_details = self.contract.base_statement.entries[0].entry_details
        self.assertEqual(len(persisted_details), len(expected_details))
        for index, (persisted_detail, expected_detail) in enumerate(
            zip(persisted_details, expected_details, strict=True)
        ):
            self.assertEqual(
                persisted_detail["source_detail_hash"],
                expected_detail.source_detail_hash,
            )
            self.assertEqual(
                Decimal(str(persisted_detail["detail_amount"])),
                expected_detail.detail_amount,
            )
            self.assertEqual(
                persisted_detail["detail_currency_code"],
                expected_detail.detail_currency_code,
            )
            if index == 0:
                self.assertEqual(
                    persisted_detail[_STRUCTURED_EVIDENCE_KEY],
                    self.contract.base_projection,
                )
            else:
                self.assertNotIn(_STRUCTURED_EVIDENCE_KEY, persisted_detail)


if __name__ == "__main__":
    unittest.main()
