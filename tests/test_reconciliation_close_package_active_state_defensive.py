"""Defensive contracts for authoritative reconciliation match-state packaging."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from contextlib import contextmanager
from types import SimpleNamespace

from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform.reconciliation_close_package import (
    ReconciliationApprovalEvidence,
    ReconciliationClosePackageInput,
    ReconciliationEvidenceReference,
    _database_owned_match_state_evidence,
)


class _Rows:
    """Minimal cursor result for database-owned match-state tests."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return the configured database rows."""
        return self._rows


class _Connection:
    """Minimal connection that records the authoritative-state query."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.parameters: tuple[object, ...] | None = None

    def execute(self, _query: str, parameters: tuple[object, ...]) -> _Rows:
        """Record parameters and return the configured rows."""
        self.parameters = parameters
        return _Rows(self.rows)


class _Ledger:
    """Tenant-bound ledger double for the public construction boundary."""

    connection = _Connection([])

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextmanager
    def _session(self):
        """Yield the configured connection like the PostgreSQL ledger session."""
        yield self.connection

    def _require_tenant(self, _connection: object) -> str:
        """Return the tenant identity used by authoritative-state lookup."""
        return "tenant-id"


class ReconciliationClosePackageActiveStateDefensiveTests(unittest.TestCase):
    """Cover every fail-closed branch around database-owned active match state."""

    @staticmethod
    def _approval(match_reference: str = "match-1") -> ReconciliationApprovalEvidence:
        return ReconciliationApprovalEvidence(
            tenant_account_reference="tenant-1",
            reconciliation_run_reference="run-1",
            reconciliation_match_reference=match_reference,
            approval_decision_code="approved",
            source_payload_hash="sha256:" + "a" * 64,
            reconciliation_snapshot_sha256="sha256:" + "b" * 64,
            evidence_reference=f"approval-{match_reference}",
        )

    @staticmethod
    def _row(
        approval: ReconciliationApprovalEvidence,
        *,
        status: str = "approved",
        decision: str = "approved",
        source_hash: str | None = None,
        source_reference: str | None = None,
        snapshot_hash: str | None = None,
    ) -> tuple[object, ...]:
        return (
            approval.reconciliation_match_reference,
            status,
            decision,
            approval.source_payload_hash if source_hash is None else source_hash,
            approval.evidence_reference if source_reference is None else source_reference,
            (
                approval.reconciliation_snapshot_sha256
                if snapshot_hash is None
                else snapshot_hash
            ),
        )

    def _load(
        self,
        rows: list[tuple[object, ...]],
        approvals: tuple[ReconciliationApprovalEvidence, ...],
    ) -> tuple[ReconciliationEvidenceReference, ...]:
        return _database_owned_match_state_evidence(
            _Connection(rows),
            "tenant-id",
            tenant_reference="tenant-1",
            reconciliation_run_reference="run-1",
            approval_evidence=approvals,
        )

    def test_empty_approval_population_requires_no_database_state_rows(self) -> None:
        self.assertEqual(self._load([], ()), ())

    def test_database_rows_must_exactly_cover_packaged_approvals(self) -> None:
        approval = self._approval()
        with self.assertRaisesRegex(ValueError, "exactly cover every packaged approval"):
            self._load([], (approval,))

    def test_match_and_approval_must_both_remain_approved(self) -> None:
        approval = self._approval()
        for row in (
            self._row(approval, status="superseded"),
            self._row(approval, decision="rejected"),
        ):
            with self.subTest(row=row):
                with self.assertRaisesRegex(ValueError, "must remain approved"):
                    self._load([row], (approval,))

    def test_database_payload_hash_must_match_packaged_approval(self) -> None:
        approval = self._approval()
        with self.assertRaisesRegex(ValueError, "payload hash"):
            self._load(
                [self._row(approval, source_hash="sha256:" + "c" * 64)],
                (approval,),
            )

    def test_database_payload_reference_must_match_packaged_approval(self) -> None:
        approval = self._approval()
        with self.assertRaisesRegex(ValueError, "payload reference"):
            self._load(
                [self._row(approval, source_reference="approval-substitute")],
                (approval,),
            )

    def test_database_snapshot_must_match_packaged_approval(self) -> None:
        approval = self._approval()
        with self.assertRaisesRegex(ValueError, "approval snapshot"):
            self._load(
                [self._row(approval, snapshot_hash="sha256:" + "d" * 64)],
                (approval,),
            )

    def test_database_state_evidence_is_deterministic_and_query_is_tenant_scoped(self) -> None:
        second = self._approval("match-2")
        first = self._approval("match-1")
        connection = _Connection([self._row(second), self._row(first)])
        state = _database_owned_match_state_evidence(
            connection,
            "tenant-id",
            tenant_reference="tenant-1",
            reconciliation_run_reference="run-1",
            approval_evidence=(second, first),
        )
        self.assertEqual(
            tuple(evidence.evidence_reference for evidence in state),
            ("match-1:approved", "match-2:approved"),
        )
        self.assertEqual(
            connection.parameters,
            ("tenant-id", "run-1", ["match-2", "match-1"]),
        )
        self.assertTrue(
            all(evidence.sha256_digest.startswith("sha256:") for evidence in state)
        )

    @staticmethod
    def _package_input() -> ReconciliationClosePackageInput:
        projection = SimpleNamespace(
            tenant_account_reference="tenant-1",
            reconciliation_run_reference="run-1",
        )
        return ReconciliationClosePackageInput(
            projection=projection,
            approval_evidence=(),
            knowledge_cutoff="2026-08-31T00:00:00Z",
            evidence_references=(
                ReconciliationEvidenceReference(
                    evidence_kind_code="reconciliation_run",
                    evidence_reference="run-1",
                    sha256_digest="sha256:" + "2" * 64,
                    knowledge_cutoff="2026-08-31T00:00:00Z",
                ),
                ReconciliationEvidenceReference(
                    evidence_kind_code="statement_artifact",
                    evidence_reference="artifact-1",
                    sha256_digest="sha256:" + "e" * 64,
                ),
                ReconciliationEvidenceReference(
                    evidence_kind_code="reconciliation_match_state",
                    evidence_reference="caller-shaped:approved",
                    sha256_digest="sha256:" + "f" * 64,
                ),
            ),
        )

    def test_public_builder_rejects_missing_database_binding_and_tenant_substitution(self) -> None:
        package_input = self._package_input()
        with self.assertRaisesRegex(ValueError, "package_input"):
            close_package.build_reconciliation_close_package(object())
        with self.assertRaisesRegex(ValueError, "database-owned match state verification"):
            close_package.build_reconciliation_close_package(package_input)
        with self.assertRaisesRegex(ValueError, "tenant must match"):
            close_package.build_reconciliation_close_package(
                package_input,
                database_url="postgresql://example",
                tenant_reference="tenant-2",
            )

    def test_public_builder_discards_caller_state_and_uses_database_owned_state(self) -> None:
        package_input = self._package_input()
        authoritative_run = next(
            evidence
            for evidence in package_input.evidence_references
            if evidence.evidence_kind_code == "reconciliation_run"
        )
        authoritative_artifact = next(
            evidence
            for evidence in package_input.evidence_references
            if evidence.evidence_kind_code == "statement_artifact"
        )
        authoritative_state = ReconciliationEvidenceReference(
            evidence_kind_code="reconciliation_match_state",
            evidence_reference="database-owned:approved",
            sha256_digest="sha256:" + "1" * 64,
        )
        sentinel = object()
        with (
            mock.patch.object(close_package, "PostgresPostingLedger", _Ledger),
            mock.patch.object(
                close_package,
                "_database_owned_match_state_evidence",
                return_value=(authoritative_state,),
            ) as state_loader,
            mock.patch.object(
                close_package,
                "_database_owned_run_source_evidence",
                return_value=(authoritative_run, authoritative_artifact),
            ) as run_loader,
            mock.patch.object(
                close_package,
                "_build_reconciliation_close_package_from_verified_state",
                return_value=sentinel,
            ) as verified_builder,
        ):
            result = close_package.build_reconciliation_close_package(
                package_input,
                database_url="postgresql://example",
                tenant_reference="tenant-1",
            )

        self.assertIs(result, sentinel)
        state_loader.assert_called_once_with(
            _Ledger.connection,
            "tenant-id",
            tenant_reference="tenant-1",
            reconciliation_run_reference="run-1",
            approval_evidence=(),
        )
        run_loader.assert_called_once_with(
            _Ledger.connection,
            "tenant-id",
            tenant_reference="tenant-1",
            reconciliation_run_reference="run-1",
        )
        verified_input = verified_builder.call_args.args[0]
        self.assertEqual(
            tuple(
                evidence.evidence_reference
                for evidence in verified_input.evidence_references
            ),
            ("run-1", "artifact-1", "database-owned:approved"),
        )


if __name__ == "__main__":
    unittest.main()
