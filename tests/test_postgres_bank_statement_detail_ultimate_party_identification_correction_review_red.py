"""Focused review RED for ultimate-party identifier-value replay admission."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    AccountingValidationError,
    MemoryArtifactStore,
    accept_bank_statement_evidence,
)
from tests import test_postgres_posting as posting
from tests.test_postgres_bank_statement_detail_ultimate_party_identification_evidence_red import (
    BankStatementDetailUltimatePartyIdentificationEvidenceRedTests as UltimatePartyHelpers,
    _CORRECTION_ERROR,
)


class BankStatementDetailUltimatePartyIdentificationCorrectionReviewRedTests(
    unittest.TestCase
):
    """Require identifier-value-only replay to reach the explicit correction boundary."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Reuse the reviewed ultimate-party fixture without duplicating its XML builder."""
        self.helper = UltimatePartyHelpers(
            "test_identifier_value_and_party52_choice_are_material_for_each_ultimate_role"
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()

    def test_identifier_value_change_reaches_complete_correction_boundary_for_each_role(
        self,
    ) -> None:
        """Accepted ultimate-party source IDs cannot be changed by silent replay."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self.helper._with_ultimate_party(
                    role,
                    "organisation",
                    self.helper.base_identifier,
                )
                changed = self.helper._with_ultimate_party(
                    role,
                    "organisation",
                    self.helper.changed_identifier,
                )
                reference = self.helper._register_statement_account(baseline)
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self.helper._command(
                        baseline,
                        reference,
                        f"{role}-identifier-value-baseline-review",
                    ),
                    posting.DATABASE_URL,
                    self.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _CORRECTION_ERROR,
                ):
                    accept_bank_statement_evidence(
                        self.helper._command(
                            changed,
                            reference,
                            f"{role}-identifier-value-changed-review",
                        ),
                        posting.DATABASE_URL,
                        self.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )


if __name__ == "__main__":
    unittest.main()
