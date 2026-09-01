"""RED contracts binding close packages to database-owned reconciliation run provenance."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from contextlib import contextmanager
from dataclasses import replace

from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform.reconciliation_close_package import (
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationCloseReviewScope,
)
from tests.test_reconciliation_close_package_cutoff_binding_red import (
    ReconciliationClosePackageCutoffBindingTests,
)


class _Ledger:
    """Tenant-bound ledger double for authoritative run-provenance tests."""

    connection = object()

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextmanager
    def _session(self):
        """Yield the configured connection like the PostgreSQL ledger session."""
        yield self.connection

    def _require_tenant(self, _connection: object) -> str:
        """Return the tenant identity used by authoritative provenance lookup."""
        return "tenant-id"


class ReconciliationClosePackageAuthoritativeRunTests(unittest.TestCase):
    """Reject caller-shaped run cutoffs, scope, run digests, and statement artifacts."""

    def setUp(self) -> None:
        fixture = ReconciliationClosePackageCutoffBindingTests(
            "test_package_rejects_cutoff_not_equal_to_immutable_run_evidence"
        )
        self.projection = fixture._projection()
        self.approvals = fixture._approval_evidence()
        self.state_evidence = tuple(
            evidence
            for evidence in fixture._evidence()
            if evidence.evidence_kind_code == "reconciliation_match_state"
        )
        self.authoritative_run = ReconciliationEvidenceReference(
            evidence_kind_code="reconciliation_run",
            evidence_reference=self.projection.reconciliation_run_reference,
            sha256_digest="sha256:" + "9" * 64,
            knowledge_cutoff="2026-08-28T08:41:54.123456Z",
        )
        self.authoritative_artifact = ReconciliationEvidenceReference(
            evidence_kind_code="statement_artifact",
            evidence_reference="artifact-store:bank-statement-1",
            sha256_digest="sha256:" + "a" * 64,
        )
        self.authoritative_scope = ReconciliationCloseReviewScope(
            tenant_account_reference=self.projection.tenant_account_reference,
            legal_entity_reference=self.projection.legal_entity_reference,
            accounting_book_reference=self.projection.accounting_book_reference,
            bank_account_assignment_reference=self.projection.bank_account_assignment_reference,
            currency_code=self.projection.currency_code,
        )
        retained = tuple(
            evidence
            for evidence in fixture._evidence()
            if evidence.evidence_kind_code
            not in {"reconciliation_run", "statement_artifact"}
        )
        self.package_input = ReconciliationClosePackageInput(
            projection=self.projection,
            approval_evidence=self.approvals,
            knowledge_cutoff=self.authoritative_run.knowledge_cutoff,
            evidence_references=retained
            + (self.authoritative_run, self.authoritative_artifact),
        )

    def _build(self, package_input: ReconciliationClosePackageInput) -> object:
        sentinel = object()
        run_loader = mock.Mock(
            return_value=(
                self.authoritative_run,
                self.authoritative_artifact,
                self.authoritative_scope,
            )
        )
        with (
            mock.patch.object(close_package, "PostgresPostingLedger", _Ledger),
            mock.patch.object(
                close_package,
                "_database_owned_match_state_evidence",
                return_value=self.state_evidence,
            ),
            mock.patch.object(
                close_package,
                "_database_owned_run_source_evidence",
                run_loader,
            ),
            mock.patch.object(
                close_package,
                "_validate_database_owned_exception_state",
            ) as exception_validator,
            mock.patch.object(
                close_package,
                "_build_reconciliation_close_package_from_verified_state",
                return_value=sentinel,
            ) as verified_builder,
        ):
            result = close_package.build_reconciliation_close_package(
                package_input,
                database_url="postgresql://example",
                tenant_reference=self.projection.tenant_account_reference,
            )
        self.assertIs(result, sentinel)
        run_loader.assert_called_once_with(
            _Ledger.connection,
            "tenant-id",
            tenant_reference=self.projection.tenant_account_reference,
            reconciliation_run_reference=self.projection.reconciliation_run_reference,
        )
        exception_validator.assert_called_once_with(
            _Ledger.connection,
            "tenant-id",
            reconciliation_run_reference=self.projection.reconciliation_run_reference,
            projection=package_input.projection,
        )
        return verified_builder.call_args.args[0]

    def test_fractional_run_cutoff_uses_run_api_precision_contract(self) -> None:
        evidence = ReconciliationEvidenceReference(
            evidence_kind_code="reconciliation_run",
            evidence_reference="run-fractional",
            sha256_digest="sha256:" + "1" * 64,
            knowledge_cutoff="2026-08-31T00:00:00.123456Z",
        )
        self.assertEqual(evidence.knowledge_cutoff, "2026-08-31T00:00:00.123456Z")

    def test_public_builder_rejects_cutoff_not_owned_by_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "database-owned reconciliation run cutoff"):
            self._build(
                replace(
                    self.package_input,
                    knowledge_cutoff="2026-08-28T08:41:55Z",
                )
            )

    def test_public_builder_rejects_projection_scope_not_owned_by_run(self) -> None:
        for field_name, value in (
            ("legal_entity_reference", "entity-substituted"),
            ("accounting_book_reference", "book-substituted"),
            ("bank_account_assignment_reference", "bank-assignment-substituted"),
            ("currency_code", "USD"),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValueError,
                    "database-owned reconciliation run scope",
                ):
                    self._build(
                        replace(
                            self.package_input,
                            projection=replace(
                                self.projection,
                                **{field_name: value},
                            ),
                        )
                    )

    def test_public_builder_rejects_unrelated_run_digest(self) -> None:
        substituted_run = replace(
            self.authoritative_run,
            sha256_digest="sha256:" + "8" * 64,
        )
        evidence = tuple(
            substituted_run
            if item.evidence_kind_code == "reconciliation_run"
            else item
            for item in self.package_input.evidence_references
        )
        with self.assertRaisesRegex(ValueError, "database-owned reconciliation run evidence"):
            self._build(replace(self.package_input, evidence_references=evidence))

    def test_public_builder_rejects_unrelated_statement_artifact(self) -> None:
        substituted_artifact = replace(
            self.authoritative_artifact,
            evidence_reference="artifact-store:unrelated",
            sha256_digest="sha256:" + "7" * 64,
        )
        evidence = tuple(
            substituted_artifact
            if item.evidence_kind_code == "statement_artifact"
            else item
            for item in self.package_input.evidence_references
        )
        with self.assertRaisesRegex(ValueError, "database-owned statement artifact evidence"):
            self._build(replace(self.package_input, evidence_references=evidence))

    def test_public_builder_replaces_caller_provenance_with_database_evidence(self) -> None:
        verified_input = self._build(self.package_input)
        run_evidence = tuple(
            evidence
            for evidence in verified_input.evidence_references
            if evidence.evidence_kind_code == "reconciliation_run"
        )
        artifact_evidence = tuple(
            evidence
            for evidence in verified_input.evidence_references
            if evidence.evidence_kind_code == "statement_artifact"
        )
        self.assertEqual(run_evidence, (self.authoritative_run,))
        self.assertEqual(artifact_evidence, (self.authoritative_artifact,))
        self.assertEqual(
            verified_input.knowledge_cutoff,
            self.authoritative_run.knowledge_cutoff,
        )


if __name__ == "__main__":
    unittest.main()
