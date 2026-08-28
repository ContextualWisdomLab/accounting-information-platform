"""RED contracts for deterministic reconciliation close-package provenance."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal

from accounting_information_platform.reconciliation_close_package import (
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    build_reconciliation_close_package,
    render_reconciliation_close_package_json,
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
            unexplained_difference_change=Decimal("-500.00"),
            outstanding_bank_items_change=Decimal("0.00"),
            outstanding_book_items_change=Decimal("500.00"),
            suitable_for_period_close_review=suitable,
            next_action=(
                "Attach this exact reconciliation evidence to the period-close review; "
                "the authorized reconciliation review remains a separate control."
            ),
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
        )

    def _input(
        self,
        *,
        suitable: bool = True,
        evidence: tuple[ReconciliationEvidenceReference, ...] | None = None,
    ) -> ReconciliationClosePackageInput:
        return ReconciliationClosePackageInput(
            projection=self._projection(suitable=suitable),
            approval_evidence_reference="approval-evidence-1",
            approval_snapshot_sha256="sha256:" + "c" * 64,
            knowledge_cutoff="2026-08-28T08:41:54Z",
            evidence_references=self._evidence() if evidence is None else evidence,
        )

    def test_package_is_order_independent_and_preserves_exact_values(self) -> None:
        first = build_reconciliation_close_package(self._input())
        second = build_reconciliation_close_package(
            self._input(evidence=tuple(reversed(self._evidence())))
        )

        self.assertRegex(first.package_sha256, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first.package_sha256, second.package_sha256)
        self.assertEqual(
            render_reconciliation_close_package_json(first),
            render_reconciliation_close_package_json(second),
        )
        payload = json.loads(render_reconciliation_close_package_json(first))
        self.assertEqual(payload["package_sha256"], first.package_sha256)
        self.assertEqual(payload["projection"]["bank_closing_balance"], "1250000.00")
        self.assertEqual(payload["projection"]["unexplained_difference"], "0.00")
        self.assertEqual(
            [item["evidence_kind_code"] for item in payload["evidence_references"]],
            ["book_population", "statement_artifact", "statement_population"],
        )
        self.assertIn("period-close", payload["next_action"])

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

        duplicate = self._evidence()[0]
        with self.assertRaisesRegex(ValueError, "unique"):
            build_reconciliation_close_package(
                self._input(evidence=(duplicate, duplicate, *self._evidence()[1:]))
            )

    def test_package_validates_approval_cutoff_and_required_evidence(self) -> None:
        cases = (
            (
                replace(self._input(), approval_evidence_reference=" approval-evidence-1"),
                "approval_evidence_reference",
            ),
            (
                replace(
                    self._input(),
                    approval_snapshot_sha256="sha256:" + "A" * 64,
                ),
                "approval_snapshot_sha256",
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
                "include immutable statement and book populations",
            ),
            (
                replace(self._input(), evidence_references=(self._evidence()[0],)),
                "statement_artifact, statement_population, and book_population",
            ),
        )
        for package_input, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(package_input)

    def test_package_binds_population_evidence_to_projection_identities(self) -> None:
        wrong_statement_population = tuple(
            replace(
                evidence,
                evidence_reference="statement-population-other",
            )
            if evidence.evidence_kind_code == "statement_population"
            else evidence
            for evidence in self._evidence()
        )
        wrong_book_population = tuple(
            replace(
                evidence,
                evidence_reference="book-population-other",
            )
            if evidence.evidence_kind_code == "book_population"
            else evidence
            for evidence in self._evidence()
        )
        duplicate_book_population = self._evidence() + (
            ReconciliationEvidenceReference(
                evidence_kind_code="book_population",
                evidence_reference="book-population-other",
                sha256_digest="sha256:" + "e" * 64,
            ),
        )

        cases = (
            (wrong_statement_population, "statement_population evidence must bind"),
            (wrong_book_population, "book_population evidence must bind"),
            (duplicate_book_population, "exactly one book_population evidence"),
        )
        for evidence, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(self._input(evidence=evidence))

    def test_package_rejects_non_finite_or_unbound_projection_evidence(self) -> None:
        projections = (
            replace(self._projection(), bank_closing_balance=Decimal("NaN")),
            replace(self._projection(), posted_book_cash_balance=Decimal("Infinity")),
            replace(self._projection(), reconciliation_run_reference=" run-2026-08"),
            replace(self._projection(), currency_code=""),
        )
        expected_errors = (
            "bank_closing_balance must be a finite Decimal",
            "posted_book_cash_balance must be a finite Decimal",
            "reconciliation_run_reference",
            "currency_code",
        )
        for projection, expected_error in zip(projections, expected_errors, strict=True):
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    build_reconciliation_close_package(
                        replace(self._input(), projection=projection)
                    )

    def test_render_fails_closed_when_package_digest_or_payload_is_tampered(self) -> None:
        baseline = build_reconciliation_close_package(self._input())
        tampered_digest = replace(
            baseline,
            package_sha256="sha256:" + "f" * 64,
        )
        tampered_projection = replace(
            baseline,
            projection=replace(
                baseline.projection,
                bank_closing_balance=Decimal("1250000.01"),
            ),
        )

        for package in (tampered_digest, tampered_projection):
            with self.assertRaisesRegex(ValueError, "package_sha256"):
                render_reconciliation_close_package_json(package)

    def test_any_approval_or_source_hash_change_changes_package_digest(self) -> None:
        baseline = build_reconciliation_close_package(self._input())
        changed_approval = build_reconciliation_close_package(
            ReconciliationClosePackageInput(
                projection=self._projection(),
                approval_evidence_reference="approval-evidence-1",
                approval_snapshot_sha256="sha256:" + "e" * 64,
                knowledge_cutoff="2026-08-28T08:41:54Z",
                evidence_references=self._evidence(),
            )
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


if __name__ == "__main__":
    unittest.main()
