"""Regression contracts for raw artifact identity on discount-order evidence."""

from __future__ import annotations

import hashlib
import unittest

from accounting_information_platform import (
    AccountingValidationError,
    accept_bank_statement_evidence,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_discount_order_evidence_red
    as discount_order_contract,
)

_PARENT_TEST = (
    discount_order_contract.BankStatementStructuredLineDiscountOrderEvidenceRedTests
)
_CORRECTION_ERROR = discount_order_contract._CORRECTION_ERROR


class BankStatementStructuredLineDiscountOrderArtifactIdentityRedTests(unittest.TestCase):
    """Bind order-sensitive artifact hashes and retained bytes to the raw payload."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL bank-statement fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare the repaired repeated-discount order fixture and fresh object store."""
        self.parent = _PARENT_TEST(
            "test_repeated_line_discount_source_order_is_material_to_each_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)

    def test_source_artifact_hashes_identify_exact_raw_payload_bytes(self) -> None:
        """Reject any order-sensitive artifact identity not computed from the source bytes."""
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
        """Prove the accepted hash key retains its exact bytes and no rejected bytes leak."""
        accepted = accept_bank_statement_evidence(
            self.parent.parent._command(
                self.parent.base_payload,
                "discount-order-artifact-identity-base",
            ),
            posting.DATABASE_URL,
            self.parent.parent.case.policy.tenant_reference,
            artifact_store=self.parent.parent.store,
        )
        self.assertFalse(accepted["replayed"])

        expected_artifacts = {
            self.parent.base_statement.source_artifact_hash: self.parent.base_payload,
        }
        self.assertEqual(self.parent.parent.store._artifacts, expected_artifacts)

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.parent.parent._command(
                    self.parent.changed_payload,
                    "discount-order-artifact-identity-changed",
                ),
                posting.DATABASE_URL,
                self.parent.parent.case.policy.tenant_reference,
                artifact_store=self.parent.parent.store,
            )

        self.assertEqual(self.parent.parent.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.parent.changed_statement.source_artifact_hash,
            self.parent.parent.store._artifacts,
        )


if __name__ == "__main__":
    unittest.main()
