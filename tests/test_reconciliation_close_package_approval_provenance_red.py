"""RED contracts for durable approval-command provenance in close packages."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal

from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform.reconciliation_close_package import (
    ReconciliationApprovalEvidence,
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    _reconciliation_match_snapshot_sha256,
    _build_reconciliation_close_package_from_verified_state as build_reconciliation_close_package,
    build_reconciliation_close_package as build_authoritative_reconciliation_close_package,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationAllocationEvidence,
    ReconciliationCloseReviewProjection,
    ReconciliationReviewedMatch,
)


class ReconciliationClosePackageApprovalProvenanceRedTests(unittest.TestCase):
    """Require approval command hashes to bind retained immutable evidence."""

    def _reviewed_match(self) -> ReconciliationReviewedMatch:
        """Return one complete exact-value reviewed match."""
        return ReconciliationReviewedMatch(
            reconciliation_match_reference="reconciliation-match-01",
            candidate_reference="candidate-01",
            candidate_statement_reference="statement-entry-01",
            candidate_journal_reference="journal-01",
            statement_amount=Decimal("100.00"),
            journal_amount=Decimal("100.00"),
            rule_code="provider_reference",
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="statement-allocation-01",
                    source_reference="statement-entry-01",
                    allocated_amount=Decimal("100.00"),
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    allocation_reference="journal-allocation-01",
                    source_reference="journal-01",
                    allocated_amount=Decimal("100.00"),
                ),
            ),
        )

    def _projection(self) -> ReconciliationCloseReviewProjection:
        """Return one close-review-eligible projection."""
        reviewed_match = self._reviewed_match()
        return ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-1",
            legal_entity_reference="entity-1",
            accounting_book_reference="book-1",
            bank_account_assignment_reference="bank-assignment-1",
            reconciliation_run_reference="run-2026-08",
            statement_population_reference="statement-population-2026-08",
            book_population_reference="book-population-2026-08",
            currency_code="KRW",
            bank_closing_balance=Decimal("100.00"),
            posted_book_cash_balance=Decimal("100.00"),
            reconciled_balance=Decimal("100.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=1,
            exception_count=0,
            exception_statement_entry_references=(),
            reviewed_match_references=(reviewed_match.reconciliation_match_reference,),
            reviewed_match_evidence=(reviewed_match,),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=(
                "Attach this exact reconciliation evidence to the period-close review; "
                "the authorized reconciliation review remains a separate control."
            ),
        )

    def _approval(self) -> ReconciliationApprovalEvidence:
        """Return database-shaped immutable approval evidence."""
        reviewed_match = self._reviewed_match()
        return ReconciliationApprovalEvidence(
            tenant_account_reference="tenant-1",
            reconciliation_run_reference="run-2026-08",
            reconciliation_match_reference=reviewed_match.reconciliation_match_reference,
            approval_decision_code="approved",
            source_payload_hash="sha256:" + "1" * 64,
            reconciliation_snapshot_sha256=_reconciliation_match_snapshot_sha256(
                "tenant-1", "run-2026-08", reviewed_match
            ),
            evidence_reference="approval-command-evidence-01",
        )

    def _source_evidence(self) -> tuple[ReconciliationEvidenceReference, ...]:
        """Return required source, approval, and current match-state evidence."""
        return (
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_artifact",
                evidence_reference="statement-artifact-01",
                sha256_digest="sha256:" + "a" * 64,
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_population",
                evidence_reference="statement-population-2026-08",
                sha256_digest="sha256:" + "b" * 64,
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="book_population",
                evidence_reference="book-population-2026-08",
                sha256_digest="sha256:" + "c" * 64,
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_run",
                evidence_reference="run-2026-08",
                sha256_digest="sha256:" + "d" * 64,
                knowledge_cutoff="2026-08-28T08:41:54Z",
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_approval_payload",
                evidence_reference="approval-command-evidence-01",
                sha256_digest="sha256:" + "1" * 64,
            ),
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_match_state",
                evidence_reference="reconciliation-match-01:approved",
                sha256_digest="sha256:" + "2" * 64,
            ),
        )

    def _input(self) -> ReconciliationClosePackageInput:
        """Return one package input with independently retained approval evidence."""
        return ReconciliationClosePackageInput(
            projection=self._projection(),
            approval_evidence=(self._approval(),),
            knowledge_cutoff="2026-08-28T08:41:54Z",
            evidence_references=self._source_evidence(),
        )

    def test_substituted_approval_hash_cannot_detach_from_retained_evidence(self) -> None:
        """A caller-shaped replacement digest must not become provenance-valid package content."""
        package_input = self._input()
        substituted = replace(
            package_input.approval_evidence[0],
            source_payload_hash="sha256:" + "f" * 64,
        )
        with self.assertRaisesRegex(ValueError, "approval source payload evidence"):
            build_reconciliation_close_package(
                replace(package_input, approval_evidence=(substituted,))
            )

    def test_approval_payload_reference_is_required_for_every_reviewed_approval(self) -> None:
        """Every packaged approval must name matching retained payload evidence."""
        package_input = self._input()
        without_approval_payload = tuple(
            evidence
            for evidence in package_input.evidence_references
            if evidence.evidence_kind_code != "reconciliation_approval_payload"
        )
        with self.assertRaisesRegex(ValueError, "approval source payload evidence"):
            build_reconciliation_close_package(
                replace(package_input, evidence_references=without_approval_payload)
            )

    def test_current_match_state_evidence_is_required_for_every_reviewed_approval(self) -> None:
        """A close package must prove each approved match is still active."""
        package_input = self._input()
        without_match_state = tuple(
            evidence
            for evidence in package_input.evidence_references
            if evidence.evidence_kind_code != "reconciliation_match_state"
        )
        with self.assertRaisesRegex(ValueError, "current match state evidence"):
            build_reconciliation_close_package(
                replace(package_input, evidence_references=without_match_state)
            )

    def test_superseded_match_state_cannot_reuse_immutable_approval(self) -> None:
        """A retired match cannot be packaged merely because its approval row remains immutable."""
        package_input = self._input()
        superseded = tuple(
            replace(
                evidence,
                evidence_reference="reconciliation-match-01:superseded",
            )
            if evidence.evidence_kind_code == "reconciliation_match_state"
            else evidence
            for evidence in package_input.evidence_references
        )
        with self.assertRaisesRegex(ValueError, "current match state evidence"):
            build_reconciliation_close_package(
                replace(package_input, evidence_references=superseded)
            )

    def test_caller_forged_approved_state_reference_is_not_authoritative(self) -> None:
        """Caller-shaped approved evidence cannot override a superseded database match."""
        package_input = self._input()
        forged_state = tuple(
            replace(evidence, sha256_digest="sha256:" + "f" * 64)
            if evidence.evidence_kind_code == "reconciliation_match_state"
            else evidence
            for evidence in package_input.evidence_references
        )
        approval = package_input.approval_evidence[0]

        class Rows:
            def fetchall(self):
                return [
                    (
                        approval.reconciliation_match_reference,
                        "superseded",
                        "approved",
                        approval.source_payload_hash,
                        approval.evidence_reference,
                        approval.reconciliation_snapshot_sha256,
                    )
                ]

        class Connection:
            def execute(self, _query, _parameters):
                return Rows()

        class Ledger:
            def __init__(self, _database_url, _tenant_reference):
                pass

            @contextmanager
            def _consistent_read_session(self):
                yield Connection()

            def _require_tenant(self, _connection):
                return "tenant-id"

        with mock.patch.object(close_package, "PostgresPostingLedger", Ledger):
            with self.assertRaisesRegex(
                ValueError,
                "active approved match population",
            ):
                build_authoritative_reconciliation_close_package(
                    replace(package_input, evidence_references=forged_state),
                    database_url="postgresql://example",
                    tenant_reference="tenant-1",
                )

    def test_matching_approval_payload_reference_and_digest_remain_packageable(self) -> None:
        """The provenance control preserves valid close-package construction."""
        package = build_reconciliation_close_package(self._input())
        self.assertRegex(package.package_sha256, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
