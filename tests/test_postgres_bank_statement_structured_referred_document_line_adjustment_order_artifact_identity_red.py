"""Regression contracts for raw artifact identity on adjustment-order evidence."""

from __future__ import annotations

import hashlib
import unittest

from accounting_information_platform import (
    AccountingValidationError,
    accept_bank_statement_evidence,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_adjustment_order_evidence_red
    as adjustment_order_contract,
)

_PARENT_TEST = (
    adjustment_order_contract.BankStatementStructuredLineAdjustmentOrderEvidenceRedTests
)
_CORRECTION_ERROR = adjustment_order_contract._CORRECTION_ERROR


class BankStatementStructuredLineAdjustmentOrderArtifactIdentityRedTests(
    unittest.TestCase
):
    """Bind adjustment-order artifact hashes and retained bytes to raw payloads."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL structured-remittance fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare the adjustment-order fixture and its fresh object store."""
        self.parent = _PARENT_TEST(
            "test_repeated_line_adjustment_source_order_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)

    def test_source_artifact_hashes_identify_exact_raw_payload_bytes(self) -> None:
        """Require each order-sensitive artifact hash to identify its exact source bytes."""
        expected_base_hash = "sha256:" + hashlib.sha256(self.parent.base_payload).hexdigest()
        expected_changed_hash = (
            "sha256:" + hashlib.sha256(self.parent.changed_payload).hexdigest()
        )

        self.assertEqual(self.parent.base_statement.source_artifact_hash, expected_base_hash)
        self.assertEqual(
            self.parent.changed_statement.source_artifact_hash,
            expected_changed_hash,
        )
        self.assertNotEqual(expected_base_hash, expected_changed_hash)

    def test_rejected_order_change_preserves_exact_accepted_raw_artifact(self) -> None:
        """Bind the accepted raw bytes before proving rejected-order non-mutation."""
        owner = self.parent.parent.credit_debit_contract
        accepted = accept_bank_statement_evidence(
            owner._command(
                self.parent.base_payload,
                "adjustment-order-artifact-identity-base",
            ),
            posting.DATABASE_URL,
            owner.case.policy.tenant_reference,
            artifact_store=owner.store,
        )
        self.assertFalse(accepted["replayed"])

        expected_artifacts = {
            self.parent.base_statement.source_artifact_hash: self.parent.base_payload,
        }
        self.assertEqual(owner.store._artifacts, expected_artifacts)

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                owner._command(
                    self.parent.changed_payload,
                    "adjustment-order-artifact-identity-changed",
                ),
                posting.DATABASE_URL,
                owner.case.policy.tenant_reference,
                artifact_store=owner.store,
            )

        self.assertEqual(owner.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.parent.changed_statement.source_artifact_hash,
            owner.store._artifacts,
        )


if __name__ == "__main__":
    unittest.main()
