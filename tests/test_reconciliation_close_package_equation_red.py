"""RED contract for reconciliation close-package bridge-equation integrity."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from accounting_information_platform.reconciliation_close_package import (
    ReconciliationApprovalEvidence,
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    _reconciliation_match_snapshot_sha256,
    _build_reconciliation_close_package_from_verified_state as build_reconciliation_close_package,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationCloseReviewProjection,
    ReconciliationAllocationEvidence,
    ReconciliationReviewedMatch,
)


class ReconciliationClosePackageEquationTests(unittest.TestCase):
    """Require close packages to re-prove the exact book-to-bank equation."""

    @staticmethod
    def _reviewed_match(index: int) -> ReconciliationReviewedMatch:
        return ReconciliationReviewedMatch(
            reconciliation_match_reference=f"reconciliation-match-{index:02d}",
            candidate_reference=f"candidate-{index:02d}",
            candidate_statement_reference=f"statement-entry-{index:02d}",
            candidate_journal_reference=f"journal-{index:02d}",
            statement_amount=Decimal("100.00"),
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference=f"statement-allocation-{index:02d}",
                    source_reference=f"statement-entry-{index:02d}",
                    allocated_amount=Decimal("100.00"),
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference=f"journal-allocation-{index:02d}",
                    source_reference=f"journal-{index:02d}",
                    allocated_amount=Decimal("100.00"),
                ),
            ),
        )

    @classmethod
    def _approval_evidence(cls) -> tuple[ReconciliationApprovalEvidence, ...]:
        return tuple(
            ReconciliationApprovalEvidence(
                tenant_account_reference="tenant-1",
                reconciliation_run_reference="run-2026-08",
                reconciliation_match_reference=f"reconciliation-match-{index:02d}",
                approval_decision_code="approved",
                source_payload_hash="sha256:" + "1234567890abcdef"[index - 1] * 64,
                reconciliation_snapshot_sha256=_reconciliation_match_snapshot_sha256(
                    "tenant-1",
                    "run-2026-08",
                    cls._reviewed_match(index),
                ),
                evidence_reference=f"approval-evidence-{index}",
            )
            for index in range(1, 9)
        )

    def test_forged_zero_difference_projection_fails_closed(self) -> None:
        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-1",
            legal_entity_reference="entity-1",
            accounting_book_reference="book-1",
            bank_account_assignment_reference="bank-assignment-1",
            reconciliation_run_reference="run-2026-08",
            statement_population_reference="statement-population-2026-08",
            book_population_reference="book-population-2026-08",
            currency_code="KRW",
            bank_closing_balance=Decimal("1250000.00"),
            posted_book_cash_balance=Decimal("1240000.00"),
            reconciled_balance=Decimal("1239999.99"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("10000.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=8,
            exception_count=0,
            exception_statement_entry_references=(),
            reviewed_match_references=tuple(
                f"reconciliation-match-{index:02d}" for index in range(1, 9)
            ),
            reviewed_match_evidence=tuple(
                self._reviewed_match(index)
                for index in range(1, 9)
            ),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=(
                "Attach this exact reconciliation evidence to the period-close review; "
                "the authorized reconciliation review remains a separate control."
            ),
        )

        approvals = self._approval_evidence()
        evidence = (
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_artifact",
                evidence_reference="statement-artifact-1",
                sha256_digest="sha256:" + "a" * 64,
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_population",
                evidence_reference="statement-population-2026-08",
                sha256_digest="sha256:" + "d" * 64,
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="book_population",
                evidence_reference="book-population-2026-08",
                sha256_digest="sha256:" + "b" * 64,
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_run",
                evidence_reference="run-2026-08",
                sha256_digest="sha256:" + "e" * 64,
                knowledge_cutoff="2026-08-28T08:41:54Z",
            ),
            *tuple(
                ReconciliationEvidenceReference(
                    evidence_kind_code="reconciliation_approval_payload",
                    evidence_reference=approval.evidence_reference,
                    sha256_digest=approval.source_payload_hash,
                )
                for approval in approvals
            ),
            *tuple(
                ReconciliationEvidenceReference(
                    evidence_kind_code="reconciliation_match_state",
                    evidence_reference=(
                        f"{approval.reconciliation_match_reference}:approved"
                    ),
                    sha256_digest="sha256:" + str(index) * 64,
                )
                for index, approval in enumerate(approvals, start=1)
            ),
        )

        with self.assertRaisesRegex(ValueError, "exact book-to-bank bridge equation"):
            build_reconciliation_close_package(
                ReconciliationClosePackageInput(
                    projection=projection,
                    approval_evidence=approvals,
                    knowledge_cutoff="2026-08-28T08:41:54Z",
                    evidence_references=evidence,
                )
            )

        valid_projection = replace(
            projection,
            reconciled_balance=Decimal("1240000.00"),
        )
        package = build_reconciliation_close_package(
            ReconciliationClosePackageInput(
                projection=valid_projection,
                approval_evidence=approvals,
                knowledge_cutoff="2026-08-28T08:41:54Z",
                evidence_references=evidence,
            )
        )
        self.assertRegex(package.package_sha256, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
