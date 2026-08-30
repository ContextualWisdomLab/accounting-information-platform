"""RED contract binding close-package cutoff to immutable reconciliation-run evidence."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from accounting_information_platform.reconciliation_close_package import (
    ReconciliationApprovalEvidence,
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    build_reconciliation_close_package,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationCloseReviewProjection,
    ReconciliationReviewedMatch,
)


class ReconciliationClosePackageCutoffBindingTests(unittest.TestCase):
    """Require one package cutoff to equal the immutable run evidence cutoff."""

    def _projection(self) -> ReconciliationCloseReviewProjection:
        return ReconciliationCloseReviewProjection(
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
            reconciled_balance=Decimal("1240000.00"),
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
                ReconciliationReviewedMatch(
                    reconciliation_match_reference=f"reconciliation-match-{index:02d}",
                    statement_entry_reference=f"statement-entry-{index:02d}",
                    journal_reference=f"journal-{index:02d}",
                    allocated_amount=Decimal("100.00"),
                )
                for index in range(1, 9)
            ),
            unexplained_difference_change=Decimal("-500.00"),
            outstanding_bank_items_change=Decimal("0.00"),
            outstanding_book_items_change=Decimal("500.00"),
            suitable_for_period_close_review=True,
            next_action=(
                "Attach this exact reconciliation evidence to the period-close review; "
                "the authorized reconciliation review remains a separate control."
            ),
        )

    def _approval_evidence(self) -> tuple[ReconciliationApprovalEvidence, ...]:
        return tuple(
            ReconciliationApprovalEvidence(
                tenant_account_reference="tenant-1",
                reconciliation_run_reference="run-2026-08",
                reconciliation_match_reference=f"reconciliation-match-{index:02d}",
                approval_decision_code="approved",
                reconciliation_snapshot_sha256="sha256:" + "abcdef12"[index - 1] * 64,
                evidence_reference=f"approval-evidence-{index}",
            )
            for index in range(1, 9)
        )

    def _evidence(self) -> tuple[ReconciliationEvidenceReference, ...]:
        return (
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_run",
                evidence_reference="run-2026-08",
                sha256_digest="sha256:" + "e" * 64,
                knowledge_cutoff="2026-08-28T08:41:54Z",
            ),
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
        )

    def test_package_rejects_cutoff_not_equal_to_immutable_run_evidence(self) -> None:
        package_input = ReconciliationClosePackageInput(
            projection=self._projection(),
            approval_evidence=self._approval_evidence(),
            knowledge_cutoff="2026-08-28T08:41:54Z",
            evidence_references=self._evidence(),
        )

        baseline = build_reconciliation_close_package(package_input)
        self.assertEqual(baseline.knowledge_cutoff, "2026-08-28T08:41:54Z")

        with self.assertRaisesRegex(
            ValueError,
            "knowledge_cutoff must match immutable reconciliation_run evidence",
        ):
            build_reconciliation_close_package(
                replace(package_input, knowledge_cutoff="2026-08-28T08:41:55Z")
            )


if __name__ == "__main__":
    unittest.main()
