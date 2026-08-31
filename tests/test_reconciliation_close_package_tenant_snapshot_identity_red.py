"""RED contract for PostgreSQL tenant identity in reconciliation approval snapshots."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from accounting_information_platform.reconciliation_close_package import (
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    _build_reconciliation_close_package_from_verified_state,
    _reconciliation_match_snapshot_sha256,
    _snapshot_tenant_identity_evidence,
    _snapshot_tenant_identity_from_evidence,
    _snapshot_value,
    verify_reconciliation_close_package,
)
from tests.test_reconciliation_close_package_cutoff_binding_red import (
    ReconciliationClosePackageCutoffBindingTests,
)


class ReconciliationClosePackageTenantSnapshotIdentityTests(unittest.TestCase):
    """Require package verification to use the internal tenant identity used by PostgreSQL."""

    @staticmethod
    def _fixture() -> tuple[
        ReconciliationClosePackageCutoffBindingTests,
        object,
    ]:
        fixture = ReconciliationClosePackageCutoffBindingTests()
        return fixture, fixture._projection()

    def test_database_snapshot_uses_bound_internal_tenant_identity(self) -> None:
        fixture, projection = self._fixture()
        internal_tenant_id = "11111111-2222-4333-8444-555555555555"
        reviewed_by_match = {
            reviewed.reconciliation_match_reference: reviewed
            for reviewed in projection.reviewed_match_evidence
        }
        approvals = tuple(
            replace(
                approval,
                reconciliation_snapshot_sha256=_reconciliation_match_snapshot_sha256(
                    internal_tenant_id,
                    approval.reconciliation_run_reference,
                    reviewed_by_match[approval.reconciliation_match_reference],
                ),
            )
            for approval in fixture._approval_evidence()
        )
        identity_payload = "\n".join(
            (
                "reconciliation_snapshot_tenant_version=1",
                "tenant_reference=" + _snapshot_value(projection.tenant_account_reference),
                "tenant_account_id=" + _snapshot_value(internal_tenant_id),
            )
        )
        tenant_identity_evidence = ReconciliationEvidenceReference(
            evidence_kind_code="reconciliation_snapshot_tenant",
            evidence_reference=internal_tenant_id,
            sha256_digest="sha256:"
            + hashlib.sha256(identity_payload.encode("utf-8")).hexdigest(),
        )
        evidence = tuple(
            replace(
                item,
                sha256_digest=next(
                    approval.source_payload_hash
                    for approval in approvals
                    if approval.evidence_reference == item.evidence_reference
                ),
            )
            if item.evidence_kind_code == "reconciliation_approval_payload"
            else item
            for item in fixture._evidence()
        ) + (tenant_identity_evidence,)
        package_input = ReconciliationClosePackageInput(
            projection=projection,
            approval_evidence=approvals,
            knowledge_cutoff="2026-08-28T08:41:54Z",
            evidence_references=evidence,
        )

        package = _build_reconciliation_close_package_from_verified_state(package_input)
        verify_reconciliation_close_package(package)
        self.assertEqual(
            next(
                item.evidence_reference
                for item in package.evidence_references
                if item.evidence_kind_code == "reconciliation_snapshot_tenant"
            ),
            internal_tenant_id,
        )

    def test_database_snapshot_tenant_binding_rejects_forged_digest(self) -> None:
        _, projection = self._fixture()
        evidence = _snapshot_tenant_identity_evidence(
            tenant_reference=projection.tenant_account_reference,
            tenant_account_id="11111111-2222-4333-8444-555555555555",
        )
        forged = replace(evidence, sha256_digest="sha256:" + "0" * 64)

        with self.assertRaisesRegex(
            ValueError,
            "must bind the public tenant reference",
        ):
            _snapshot_tenant_identity_from_evidence((forged,), projection=projection)

    def test_database_snapshot_tenant_binding_rejects_ambiguous_identity(self) -> None:
        _, projection = self._fixture()
        first = _snapshot_tenant_identity_evidence(
            tenant_reference=projection.tenant_account_reference,
            tenant_account_id="11111111-2222-4333-8444-555555555555",
        )
        second = _snapshot_tenant_identity_evidence(
            tenant_reference=projection.tenant_account_reference,
            tenant_account_id="66666666-7777-4888-8999-aaaaaaaaaaaa",
        )

        with self.assertRaisesRegex(ValueError, "at most one"):
            _snapshot_tenant_identity_from_evidence(
                (first, second),
                projection=projection,
            )

    def test_pure_verifier_fallback_remains_public_tenant_reference(self) -> None:
        _, projection = self._fixture()
        self.assertEqual(
            _snapshot_tenant_identity_from_evidence((), projection=projection),
            projection.tenant_account_reference,
        )
        self.assertEqual(
            _snapshot_tenant_identity_from_evidence([], projection=projection),
            projection.tenant_account_reference,
        )


if __name__ == "__main__":
    unittest.main()
