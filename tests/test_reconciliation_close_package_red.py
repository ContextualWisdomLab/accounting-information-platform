"""RED contracts for deterministic reconciliation close-package provenance."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal

from accounting_information_platform.reconciliation_close_package import (
    ReconciliationApprovalEvidence,
    ReconciliationClosePackage,
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    build_reconciliation_close_package,
    render_reconciliation_close_package_json,
    verify_reconciliation_close_package,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationCloseReviewProjection,
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
                reconciliation_snapshot_sha256="sha256:" + "abcdef12"[index - 1] * 64,
                evidence_reference=f"approval-evidence-{index}",
            )
            for index in range(1, 9)
        )

    def _evidence(self) -> tuple[ReconciliationEvidenceReference, ...]:
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
        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(payload["package_sha256"], first.package_sha256)
        self.assertEqual(payload["projection"]["bank_closing_balance"], "1250000.00")
        self.assertEqual(payload["projection"]["unexplained_difference"], "0.00")
        self.assertEqual(
            [item["reconciliation_match_reference"] for item in payload["approval_evidence"]],
            list(first.projection.reviewed_match_references),
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

    def test_approval_evidence_requires_canonical_decision_and_structure(self) -> None:
        with self.assertRaisesRegex(ValueError, "approval_decision_code"):
            ReconciliationApprovalEvidence(
                tenant_account_reference="tenant-1",
                reconciliation_run_reference="run-2026-08",
                reconciliation_match_reference="reconciliation-match-01",
                approval_decision_code="pending",
                reconciliation_snapshot_sha256="sha256:" + "a" * 64,
                evidence_reference="approval-evidence-1",
            )

        for approval_evidence, expected_error in (
            ([], "non-empty tuple"),
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

        with self.assertRaisesRegex(ValueError, "ReconciliationCloseReviewProjection"):
            build_reconciliation_close_package(
                replace(self._input(), projection=object())  # type: ignore[arg-type]
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

        for package in (
            tampered_digest,
            malformed_digest,
            tampered_projection,
            equation_tampered,
            tampered_next_action,
            tampered_approval,
            tampered_order,
        ):
            with self.subTest(package=package):
                with self.assertRaisesRegex(ValueError, "package_sha256"):
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
        changed_approval = build_reconciliation_close_package(
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

        self.assertNotEqual(baseline.package_sha256, changed_approval.package_sha256)
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
