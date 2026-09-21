"""Restricted-runtime baseline regression for referred-document contraction."""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
