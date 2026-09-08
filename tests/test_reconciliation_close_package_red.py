"""RED contracts for deterministic reconciliation close-package provenance."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal, localcontext

from accounting_information_platform.reconciliation_close_package import (
    ReconciliationApprovalEvidence,
    ReconciliationClosePackage,
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    _reconciliation_match_snapshot_sha256,
    _build_reconciliation_close_package_from_verified_state as build_reconciliation_close_package,
    render_reconciliation_close_package_json,
    verify_reconciliation_close_package,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationCloseReviewProjection,
    ReconciliationAllocationEvidence,
    ReconciliationReviewedMatch,
)


class ReconciliationClosePackageTests(unittest.TestCase):
    """Require tamper-evident, exact-value close evidence without posting authority."""

    def _projection(
        self,
        *,
        suitable: bool = True,
    ) -> ReconciliationCloseReviewProjection:
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
                for index in range(1, 9)
            ),
            unexplained_difference_change=Decimal("-500.00"),
            outstanding_bank_items_change=Decimal("0.00"),
            outstanding_book_items_change=Decimal("500.00"),
            suitable_for_period_close_review=suitable,
            next_action=(
                "Attach this exact reconciliation evidence to the period-close review; "
                "the authorized reconciliation review remains a separate control."
            ),
        )

    def _approval_evidence(
        self,
        *,
        decision_code: str = "approved",
        tenant: str = "tenant-1",
        run: str = "run-2026-08",
    ) -> tuple[ReconciliationApprovalEvidence, ...]:
        return tuple(
            ReconciliationApprovalEvidence(
                tenant_account_reference=tenant,
                reconciliation_run_reference=run,
                reconciliation_match_reference=f"reconciliation-match-{index:02d}",
                approval_decision_code=decision_code,
                source_payload_hash="sha256:" + "1234567890abcdef"[index - 1] * 64,
                reconciliation_snapshot_sha256=_reconciliation_match_snapshot_sha256(
                    "tenant-1",
                    "run-2026-08",
                    self._projection().reviewed_match_evidence[index - 1],
                ),
                evidence_reference=f"approval-evidence-{index}",
            )
            for index in range(1, 9)
        )

    def _base_evidence(self) -> tuple[ReconciliationEvidenceReference, ...]:
        """Return required non-approval source evidence for one baseline run."""
        return (
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
        )

    def _approval_payload_evidence(
        self,
        approvals: tuple[ReconciliationApprovalEvidence, ...] | None = None,
    ) -> tuple[ReconciliationEvidenceReference, ...]:
        """Retain approval payload digests and current approved match-state evidence."""
        approval_evidence = self._approval_evidence() if approvals is None else approvals
        payload_evidence = tuple(
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_approval_payload",
                evidence_reference=approval.evidence_reference,
                sha256_digest=approval.source_payload_hash,
            )
            for approval in approval_evidence
        )
        match_state_evidence = tuple(
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_match_state",
                evidence_reference=f"{approval.reconciliation_match_reference}:approved",
                sha256_digest=approval.reconciliation_snapshot_sha256,
            )
            for approval in approval_evidence
        )
        return payload_evidence + match_state_evidence

    def _evidence(self) -> tuple[ReconciliationEvidenceReference, ...]:
        return self._base_evidence() + self._approval_payload_evidence()

    def _input(
        self,
        *,
        suitable: bool = True,
        evidence: tuple[ReconciliationEvidenceReference, ...] | None = None,
    ) -> ReconciliationClosePackageInput:
        return ReconciliationClosePackageInput(
            projection=self._projection(suitable=suitable),
            approval_evidence=self._approval_evidence(),
            knowledge_cutoff="2026-08-28T08:41:54Z",
            evidence_references=self._evidence() if evidence is None else evidence,
        )

    def test_package_is_order_independent_and_preserves_exact_values(self) -> None:
        first = build_reconciliation_close_package(self._input())
        second = build_reconciliation_close_package(
            replace(
                self._input(evidence=tuple(reversed(self._evidence()))),
                approval_evidence=tuple(reversed(self._approval_evidence())),
            )
        )

        self.assertRegex(first.package_sha256, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first.package_sha256, second.package_sha256)
        self.assertEqual(
            render_reconciliation_close_package_json(first),
            render_reconciliation_close_package_json(second),
        )
        payload = json.loads(render_reconciliation_close_package_json(first))
        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(first.projection.reviewed_match_evidence[0].statement_allocations[0].source_capacity, None)
        self.assertEqual(payload["package_sha256"], first.package_sha256)
        self.assertEqual(payload["projection"]["schema_version"], 2)
        self.assertEqual(payload["projection"]["bank_closing_balance"], "1250000.00")
        self.assertEqual(payload["projection"]["unexplained_difference"], "0.00")
        self.assertEqual(
            [item["reconciliation_match_reference"] for item in payload["approval_evidence"]],
            list(first.projection.reviewed_match_references),
        )
        self.assertEqual(
            payload["projection"]["reviewed_match_evidence"][0],
            {
                "reconciliation_match_reference": "reconciliation-match-01",
                "candidate_journal_reference": "journal-01",
                "candidate_reference": "candidate-01",
                "candidate_statement_reference": "statement-entry-01",
                "journal_allocations": [
                    {
                        "allocated_amount": "100.00",
                        "allocation_reference": "journal-allocation-01",
                        "source_reference": "journal-01",
                    }
                ],
                "journal_amount": "100.00",
                "rule_code": "provider_reference",
                "statement_allocations": [
                    {
                        "allocated_amount": "100.00",
                        "allocation_reference": "statement-allocation-01",
                        "source_reference": "statement-entry-01",
                    }
                ],
                "statement_amount": "100.00",
            },
        )
        self.assertTrue(
            all(
                item["approval_decision_code"] == "approved"
                for item in payload["approval_evidence"]
            )
        )
        self.assertEqual(
            [item["evidence_kind_code"] for item in payload["evidence_references"]],
            [
                "book_population",
                *["reconciliation_approval_payload"] * 8,
                *["reconciliation_match_state"] * 8,
                "reconciliation_run",
                "statement_artifact",
                "statement_population",
            ],
        )
        run_evidence = next(
            item
            for item in payload["evidence_references"]
            if item["evidence_kind_code"] == "reconciliation_run"
        )
        self.assertEqual(run_evidence["knowledge_cutoff"], payload["knowledge_cutoff"])
        self.assertIn("period-close", payload["next_action"])

    def test_zero_activity_reconciliation_accepts_empty_approval_evidence(self) -> None:
        """A clean reconciliation with no matches still has packageable evidence."""
        projection = replace(
            self._projection(),
            safely_matchable_candidate_count=0,
            reviewed_match_references=(),
            reviewed_match_evidence=(),
        )
        package = build_reconciliation_close_package(
            replace(
                self._input(),
                projection=projection,
                approval_evidence=(),
                evidence_references=self._base_evidence(),
            )
        )
        self.assertEqual(package.approval_evidence, ())

    def test_package_equation_preserves_large_decimals_under_low_precision(self) -> None:
        """Close-package validation must not round a valid high-precision bridge."""
        projection = replace(
            self._projection(),
            bank_closing_balance=Decimal("12345678901234567890123457.000000"),
            posted_book_cash_balance=Decimal("12345678901234567890123456.000000"),
            reconciled_balance=Decimal("12345678901234567890123456.000000"),
            outstanding_book_items=Decimal("1.000000"),
            outstanding_bank_items=Decimal("0.000000"),
        )
        with localcontext() as context:
            context.prec = 6
            package = build_reconciliation_close_package(
                replace(self._input(), projection=projection)
            )
        self.assertRegex(package.package_sha256, r"^sha256:[0-9a-f]{64}$")

    def test_projection_facts_cannot_be_substituted_under_same_match_identity(self) -> None:
        """A snapshot-bound approval must reject caller-shaped reviewed facts."""
        projection = self._projection()
        original = projection.reviewed_match_evidence[0]
        for replacement in (
            replace(
                original,
                candidate_statement_reference="statement-entry-substitute",
                statement_allocations=(
                    replace(
                        original.statement_allocations[0],
                        source_reference="statement-entry-substitute",
                    ),
                ),
            ),
            replace(
                original,
                candidate_journal_reference="journal-substitute",
                journal_allocations=(
                    replace(
                        original.journal_allocations[0],
                        source_reference="journal-substitute",
                    ),
                ),
            ),
            replace(
                original,
                statement_amount=Decimal("99.00"),
                journal_amount=Decimal("99.00"),
                statement_allocations=(
                    replace(
                        original.statement_allocations[0],
                        allocated_amount=Decimal("99.00"),
                    ),
                ),
                journal_allocations=(
                    replace(
                        original.journal_allocations[0],
                        allocated_amount=Decimal("99.00"),
                    ),
                ),
            ),
        ):
            with self.subTest(replacement=replacement):
                substituted_projection = replace(
                    projection,
                    reviewed_match_evidence=(
                        replacement,
                        *projection.reviewed_match_evidence[1:],
                    ),
                )
                with self.assertRaisesRegex(ValueError, "snapshot|reviewed match"):
                    build_reconciliation_close_package(
                        replace(self._input(), projection=substituted_projection)
                    )

    def test_package_preserves_split_allocation_population(self) -> None:
        """Package evidence keeps every allocation row and its authoritative capacity."""
        original = self._projection().reviewed_match_evidence[0]
        split = replace(
            original,
            reconciliation_match_reference="reconciliation-match-split",
            candidate_reference="candidate-split",
            candidate_statement_reference="statement-entry-split",
            candidate_journal_reference="journal-split-01",
            statement_amount=Decimal("150.00"),
            journal_amount=Decimal("100.00"),
            statement_allocations=(
                ReconciliationAllocationEvidence(
                    "statement-allocation-split",
                    "statement-entry-split",
                    Decimal("150.00"),
                    Decimal("150.00"),
                ),
            ),
            journal_allocations=(
                ReconciliationAllocationEvidence(
                    "journal-allocation-01",
                    "journal-split-01",
                    Decimal("100.00"),
                    Decimal("100.00"),
                ),
                ReconciliationAllocationEvidence(
                    "journal-allocation-02",
                    "journal-split-02",
                    Decimal("50.00"),
                    Decimal("50.00"),
                ),
            ),
        )
        projection = replace(
            self._projection(),
            safely_matchable_candidate_count=1,
            reviewed_match_references=("reconciliation-match-split",),
            reviewed_match_evidence=(split,),
        )
        approval = ReconciliationApprovalEvidence(
            tenant_account_reference="tenant-1",
            reconciliation_run_reference="run-2026-08",
            reconciliation_match_reference="reconciliation-match-split",
            approval_decision_code="approved",
            source_payload_hash="sha256:" + "1" * 64,
            reconciliation_snapshot_sha256=_reconciliation_match_snapshot_sha256(
                "tenant-1", "run-2026-08", split
            ),
            evidence_reference="approval-evidence-split",
        )
        package = build_reconciliation_close_package(
            replace(
                self._input(),
                projection=projection,
                approval_evidence=(approval,),
                evidence_references=(
                    *self._base_evidence(),
                    *self._approval_payload_evidence((approval,)),
                ),
            )
        )

        self.assertEqual(
            len(package.projection.reviewed_match_evidence[0].journal_allocations),
            2,
        )
        payload = json.loads(render_reconciliation_close_package_json(package))
        self.assertEqual(payload["projection"]["schema_version"], 3)
        self.assertEqual(
            payload["projection"]["reviewed_match_evidence"][0]["journal_allocations"][1][
                "source_capacity"
            ],
            "50.00",
        )

    def test_package_requires_candidate_sources_in_allocation_population(self) -> None:
        """Candidate source identities must be represented by allocation rows."""
        original = self._projection().reviewed_match_evidence[0]
        mismatched = replace(
            original,
            candidate_statement_reference="statement-entry-not-allocated",
        )
        projection = replace(
            self._projection(),
            safely_matchable_candidate_count=1,
            reviewed_match_references=(mismatched.reconciliation_match_reference,),
            reviewed_match_evidence=(mismatched,),
        )
        approval = ReconciliationApprovalEvidence(
            tenant_account_reference="tenant-1",
            reconciliation_run_reference="run-2026-08",
            reconciliation_match_reference=mismatched.reconciliation_match_reference,
            approval_decision_code="approved",
            source_payload_hash="sha256:" + "1" * 64,
            reconciliation_snapshot_sha256=_reconciliation_match_snapshot_sha256(
                "tenant-1", "run-2026-08", mismatched
            ),
            evidence_reference="approval-evidence-mismatched-source",
        )

        with self.assertRaisesRegex(ValueError, "candidate source identities"):
            build_reconciliation_close_package(
                replace(
                    self._input(),
                    projection=projection,
                    approval_evidence=(approval,),
                )
            )

    def test_approval_evidence_requires_canonical_decision_and_structure(self) -> None:
        with self.assertRaisesRegex(ValueError, "approval_decision_code"):
            ReconciliationApprovalEvidence(
                tenant_account_reference="tenant-1",
                reconciliation_run_reference="run-2026-08",
                reconciliation_match_reference="reconciliation-match-01",
                approval_decision_code="pending",
                source_payload_hash="sha256:" + "a" * 64,
                reconciliation_snapshot_sha256="sha256:" + "a" * 64,
                evidence_reference="approval-evidence-1",
            )
        with self.assertRaisesRegex(ValueError, "source_payload_hash"):
            ReconciliationApprovalEvidence(
                tenant_account_reference="tenant-1",
                reconciliation_run_reference="run-2026-08",
                reconciliation_match_reference="reconciliation-match-01",
                approval_decision_code="approved",
                source_payload_hash="not-a-digest",
                reconciliation_snapshot_sha256="sha256:" + "a" * 64,
                evidence_reference="approval-evidence-1",
            )

        for approval_evidence, expected_error in (
            ([], "must be a tuple"),
            ((object(),), "structured evidence objects"),
            (
                (
                    self._approval_evidence()[0],
                    replace(
                        self._approval_evidence()[1],
                        reconciliation_match_reference="reconciliation-match-01",
                    ),
                    *self._approval_evidence()[2:],
                ),
                "match identities must be unique",
            ),
        ):
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(
                        replace(self._input(), approval_evidence=approval_evidence)
                    )

    def test_package_fails_closed_for_every_close_review_ineligibility_signal(self) -> None:
        projections = (
            self._projection(suitable=False),
            replace(self._projection(), exception_count=1),
            replace(self._projection(), unexplained_difference=Decimal("0.01")),
            replace(
                self._projection(),
                exception_statement_entry_references=("statement-entry-exception",),
            ),
        )
        for projection in projections:
            with self.subTest(projection=projection):
                with self.assertRaisesRegex(
                    ValueError, "not suitable for period-close review"
                ):
                    build_reconciliation_close_package(
                        replace(self._input(), projection=projection)
                    )

    def test_package_fails_closed_for_invalid_or_duplicate_evidence_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence_kind_code"):
            ReconciliationEvidenceReference(
                evidence_kind_code=" statement_artifact",
                evidence_reference="statement-artifact-1",
                sha256_digest="sha256:" + "a" * 64,
            )
        with self.assertRaisesRegex(ValueError, "evidence_reference"):
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_artifact",
                evidence_reference="",
                sha256_digest="sha256:" + "a" * 64,
            )
        with self.assertRaisesRegex(ValueError, "sha256_digest"):
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_artifact",
                evidence_reference="statement-artifact-1",
                sha256_digest="not-a-digest",
            )
        with self.assertRaisesRegex(ValueError, "permitted only on reconciliation_run"):
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_artifact",
                evidence_reference="statement-artifact-1",
                sha256_digest="sha256:" + "a" * 64,
                knowledge_cutoff="2026-08-28T08:41:54Z",
            )
        with self.assertRaisesRegex(ValueError, "canonical UTC RFC 3339"):
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_run",
                evidence_reference="run-2026-08",
                sha256_digest="sha256:" + "e" * 64,
            )

        duplicate = self._evidence()[0]
        with self.assertRaisesRegex(ValueError, "unique"):
            build_reconciliation_close_package(
                self._input(evidence=(duplicate, duplicate, *self._evidence()[1:]))
            )

    def test_package_validates_approval_cutoff_and_required_evidence(self) -> None:
        cases = (
            (
                replace(self._input(), approval_evidence=()),
                "approval evidence",
            ),
            (
                replace(self._input(), knowledge_cutoff="2026-08-28 08:41:54"),
                "canonical UTC RFC 3339",
            ),
            (
                replace(self._input(), knowledge_cutoff="2026-02-30T08:41:54Z"),
                "real UTC calendar instant",
            ),
            (
                replace(self._input(), evidence_references=()),
                "include immutable reconciliation run, statement, and book evidence",
            ),
            (
                replace(self._input(), evidence_references=(self._evidence()[0],)),
                "reconciliation_run, statement_artifact, statement_population, and book_population",
            ),
        )
        for package_input, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(package_input)

    def test_package_binds_run_and_population_evidence_to_projection_identities(self) -> None:
        wrong_run = tuple(
            replace(evidence, evidence_reference="run-other")
            if evidence.evidence_kind_code == "reconciliation_run"
            else evidence
            for evidence in self._evidence()
        )
        wrong_statement_population = tuple(
            replace(evidence, evidence_reference="statement-population-other")
            if evidence.evidence_kind_code == "statement_population"
            else evidence
            for evidence in self._evidence()
        )
        wrong_book_population = tuple(
            replace(evidence, evidence_reference="book-population-other")
            if evidence.evidence_kind_code == "book_population"
            else evidence
            for evidence in self._evidence()
        )
        duplicate_run = self._evidence() + (
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_run",
                evidence_reference="run-other",
                sha256_digest="sha256:" + "f" * 64,
                knowledge_cutoff="2026-08-28T08:41:54Z",
            ),
        )
        duplicate_statement_population = self._evidence() + (
            ReconciliationEvidenceReference(
                evidence_kind_code="statement_population",
                evidence_reference="statement-population-other",
                sha256_digest="sha256:" + "f" * 64,
            ),
        )
        duplicate_book_population = self._evidence() + (
            ReconciliationEvidenceReference(
                evidence_kind_code="book_population",
                evidence_reference="book-population-other",
                sha256_digest="sha256:" + "f" * 64,
            ),
        )

        cases = (
            (wrong_run, "reconciliation_run evidence must bind"),
            (wrong_statement_population, "statement_population evidence must bind"),
            (wrong_book_population, "book_population evidence must bind"),
            (duplicate_run, "exactly one reconciliation_run evidence"),
            (
                duplicate_statement_population,
                "exactly one statement_population evidence",
            ),
            (duplicate_book_population, "exactly one book_population evidence"),
        )
        for evidence, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(self._input(evidence=evidence))

    def test_package_requires_complete_approved_match_evidence_in_exact_scope(self) -> None:
        unrelated_run = replace(
            self._approval_evidence()[0],
            reconciliation_run_reference="run-other",
        )
        unrelated_match = replace(
            self._approval_evidence()[0],
            reconciliation_match_reference="reconciliation-match-other",
        )
        rejected = tuple(
            replace(evidence, approval_decision_code="rejected")
            for evidence in self._approval_evidence()
        )

        cases = (
            (tuple((unrelated_run, *self._approval_evidence()[1:])), "same tenant and run"),
            (tuple((unrelated_match, *self._approval_evidence()[1:])), "reviewed match population"),
            (rejected, "approved"),
            (self._approval_evidence()[:-1], "exactly cover"),
        )
        for approval_evidence, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(
                        replace(self._input(), approval_evidence=approval_evidence)
                    )

    def test_package_rejects_non_finite_or_unbound_projection_evidence(self) -> None:
        projections = (
            replace(self._projection(), bank_closing_balance=Decimal("NaN")),
            replace(self._projection(), posted_book_cash_balance=Decimal("Infinity")),
            replace(self._projection(), reconciled_balance=1.0),
            replace(
                self._projection(),
                unexplained_difference_change=Decimal("NaN"),
            ),
            replace(self._projection(), reconciliation_run_reference=" run-2026-08"),
            replace(self._projection(), currency_code=""),
            replace(self._projection(), safely_matchable_candidate_count=True),
            replace(self._projection(), safely_matchable_candidate_count=-1),
            replace(self._projection(), exception_count=True),
            replace(self._projection(), reviewed_match_references=[]),
            replace(self._projection(), reviewed_match_references=(" bad",)),
            replace(
                self._projection(),
                reviewed_match_references=(
                    "duplicate",
                    "duplicate",
                    *self._projection().reviewed_match_references[2:],
                ),
            ),
            replace(self._projection(), reviewed_match_references=()),
            replace(
                self._projection(),
                exception_statement_entry_references=(" bad-reference",),
            ),
            replace(
                self._projection(),
                exception_statement_entry_references=("duplicate", "duplicate"),
            ),
        )
        expected_errors = (
            "bank_closing_balance must be a finite Decimal",
            "posted_book_cash_balance must be a finite Decimal",
            "reconciled_balance must be a finite Decimal",
            "unexplained_difference_change must be None or a finite Decimal",
            "reconciliation_run_reference",
            "currency_code",
            "safely_matchable_candidate_count",
            "safely_matchable_candidate_count",
            "exception_count",
            "reviewed match identities must be a tuple",
            "reviewed match identities must be canonical",
            "reviewed match identities must be unique",
            "reviewed match identities must exactly cover",
            "exception_statement_entry_references must contain canonical",
            "exception_statement_entry_references must be unique",
        )
        for projection, expected_error in zip(projections, expected_errors, strict=True):
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(
                        replace(self._input(), projection=projection)
                    )

    def test_package_requires_complete_structured_reviewed_match_evidence(self) -> None:
        valid_evidence = self._projection().reviewed_match_evidence
        cases = (
            (replace(self._projection(), reviewed_match_evidence=[]), "must be a tuple"),
            (
                replace(self._projection(), reviewed_match_evidence=("not-structured",)),
                "structured evidence objects",
            ),
            (replace(self._projection(), reviewed_match_evidence=()), "exactly cover"),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0],
                            statement_allocations=(
                                replace(
                                    valid_evidence[0].statement_allocations[0],
                                    allocated_amount=1.0,
                                ),
                            ),
                        ),
                        *valid_evidence[1:],
                    ),
                ),
                "positive exact Decimal",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0],
                            statement_allocations=(
                                replace(
                                    valid_evidence[0].statement_allocations[0],
                                    allocated_amount=Decimal("NaN"),
                                ),
                            ),
                        ),
                        *valid_evidence[1:],
                    ),
                ),
                "positive exact Decimal",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0],
                            statement_allocations=(
                                replace(
                                    valid_evidence[0].statement_allocations[0],
                                    allocated_amount=Decimal("0"),
                                ),
                            ),
                        ),
                        *valid_evidence[1:],
                    ),
                ),
                "positive exact Decimal",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0],
                            reconciliation_match_reference=" other",
                        ),
                        *valid_evidence[1:],
                    ),
                ),
                "reviewed match reconciliation_match_reference",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0],
                            candidate_statement_reference="statement-entry-substitute",
                        ),
                        *valid_evidence[1:],
                    ),
                    reviewed_match_references=(
                        "different-match",
                        *self._projection().reviewed_match_references[1:],
                    ),
                ),
                "bind projection match identities",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(valid_evidence[0], statement_amount=Decimal("0")),
                        *valid_evidence[1:],
                    ),
                ),
                "positive exact Decimals",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(valid_evidence[0], statement_allocations=()),
                        *valid_evidence[1:],
                    ),
                ),
                "non-empty tuples",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0], statement_allocations=("not-structured",)
                        ),
                        *valid_evidence[1:],
                    ),
                ),
                "structured evidence objects",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0],
                            statement_allocations=(
                                valid_evidence[0].statement_allocations[0],
                                replace(
                                    valid_evidence[0].statement_allocations[0],
                                    allocation_reference="statement-allocation-00",
                                    source_reference="statement-entry-00",
                                ),
                            ),
                        ),
                        *valid_evidence[1:],
                    ),
                ),
                "deterministic ordering",
            ),
            (
                replace(
                    self._projection(),
                    reviewed_match_evidence=(
                        replace(
                            valid_evidence[0],
                            statement_allocations=(
                                valid_evidence[0].statement_allocations[0],
                                replace(
                                    valid_evidence[0].statement_allocations[0],
                                    source_reference="statement-entry-00",
                                ),
                            ),
                        ),
                        *valid_evidence[1:],
                    ),
                ),
                "identities must be unique",
            ),
        )
        for projection, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(
                        replace(self._input(), projection=projection)
                    )

    def test_package_rejects_caller_supplied_next_action(self) -> None:
        """Close-package guidance cannot be replaced with an unauthorized action."""
        projection = replace(self._projection(), next_action="Approve and post this reconciliation")

        with self.assertRaisesRegex(ValueError, "next action"):
            build_reconciliation_close_package(replace(self._input(), projection=projection))

        with self.assertRaisesRegex(ValueError, "ReconciliationCloseReviewProjection"):
            build_reconciliation_close_package(
                replace(self._input(), projection=object())  # type: ignore[arg-type]
            )

    def test_package_rejects_malformed_public_input_with_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "ReconciliationClosePackageInput"):
            build_reconciliation_close_package(object())  # type: ignore[arg-type]

    def test_package_revalidates_large_decimal_bridge_without_rounding(self) -> None:
        huge_balance = Decimal("100000000000000000000000.000000")
        projection = replace(
            self._projection(),
            bank_closing_balance=huge_balance,
            posted_book_cash_balance=huge_balance,
            reconciled_balance=huge_balance,
            outstanding_bank_items=Decimal("0.000000"),
            outstanding_book_items=Decimal("0.000001"),
            unexplained_difference=Decimal("0.000000"),
        )

        with self.assertRaisesRegex(ValueError, "exact book-to-bank bridge equation"):
            build_reconciliation_close_package(
                replace(self._input(), projection=projection)
            )

    def test_package_rejects_noncanonical_evidence_container_or_member(self) -> None:
        with self.assertRaisesRegex(ValueError, "immutable reconciliation run, statement, and book"):
            build_reconciliation_close_package(
                replace(self._input(), evidence_references=[])  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "evidence reference objects"):
            build_reconciliation_close_package(
                replace(
                    self._input(),
                    evidence_references=(object(),),  # type: ignore[arg-type]
                )
            )
        without_artifact = tuple(
            evidence
            for evidence in self._evidence()
            if evidence.evidence_kind_code != "statement_artifact"
        )
        with self.assertRaisesRegex(
            ValueError,
            "reconciliation_run, statement_artifact, statement_population, and book_population",
        ):
            build_reconciliation_close_package(self._input(evidence=without_artifact))

    def test_render_fails_closed_when_package_digest_or_payload_is_tampered(self) -> None:
        baseline = build_reconciliation_close_package(self._input())
        tampered_digest = replace(
            baseline,
            package_sha256="sha256:" + "f" * 64,
        )
        malformed_digest = replace(baseline, package_sha256="not-a-digest")
        tampered_projection = replace(
            baseline,
            projection=replace(
                baseline.projection,
                bank_closing_balance=Decimal("1250000.01"),
            ),
        )
        equation_tampered = replace(
            baseline,
            projection=replace(
                baseline.projection,
                reconciled_balance=Decimal("1239999.99"),
            ),
        )
        tampered_next_action = replace(baseline, next_action="Archive somewhere else.")
        tampered_approval = replace(
            baseline,
            approval_evidence=(
                replace(
                    baseline.approval_evidence[0],
                    reconciliation_snapshot_sha256="sha256:" + "f" * 64,
                ),
                *baseline.approval_evidence[1:],
            ),
        )
        tampered_order = replace(
            baseline,
            evidence_references=tuple(reversed(baseline.evidence_references)),
        )
        tampered_approval_order = replace(
            baseline,
            approval_evidence=tuple(reversed(baseline.approval_evidence)),
        )

        for package in (
            tampered_digest,
            malformed_digest,
            tampered_projection,
            equation_tampered,
            tampered_next_action,
            tampered_approval,
            tampered_order,
            tampered_approval_order,
        ):
            with self.subTest(package=package):
                with self.assertRaisesRegex(ValueError, "package_sha256|snapshot"):
                    render_reconciliation_close_package_json(package)

        with self.assertRaisesRegex(ValueError, "ReconciliationClosePackage"):
            verify_reconciliation_close_package(object())  # type: ignore[arg-type]

        invalid_cutoff = replace(baseline, knowledge_cutoff="not-a-cutoff")
        with self.assertRaisesRegex(ValueError, "canonical UTC RFC 3339"):
            verify_reconciliation_close_package(invalid_cutoff)

    def test_any_approval_or_source_hash_change_changes_package_digest(self) -> None:
        baseline = build_reconciliation_close_package(self._input())
        changed_approval_evidence = tuple(
            replace(
                evidence,
                reconciliation_snapshot_sha256="sha256:" + "f" * 64,
            )
            if evidence.reconciliation_match_reference == "reconciliation-match-01"
            else evidence
            for evidence in self._approval_evidence()
        )
        with self.assertRaisesRegex(ValueError, "snapshot"):
            build_reconciliation_close_package(
                replace(self._input(), approval_evidence=changed_approval_evidence)
            )
        changed_source = build_reconciliation_close_package(
            self._input(
                evidence=(
                    ReconciliationEvidenceReference(
                        evidence_kind_code="statement_artifact",
                        evidence_reference="statement-artifact-1",
                        sha256_digest="sha256:" + "f" * 64,
                    ),
                    *self._evidence()[1:],
                )
            )
        )

        self.assertNotEqual(baseline.package_sha256, changed_source.package_sha256)

    def test_direct_package_construction_still_requires_canonical_integrity(self) -> None:
        baseline = build_reconciliation_close_package(self._input())
        direct = ReconciliationClosePackage(
            projection=baseline.projection,
            approval_evidence=baseline.approval_evidence,
            knowledge_cutoff=baseline.knowledge_cutoff,
            evidence_references=baseline.evidence_references,
            package_sha256=baseline.package_sha256,
            next_action=baseline.next_action,
        )
        verify_reconciliation_close_package(direct)


if __name__ == "__main__":
    unittest.main()
