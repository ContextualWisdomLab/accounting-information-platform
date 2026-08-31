"""RED tests for database-owned close-package run and statement provenance."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from accounting_information_platform import reconciliation_close_package as close_package


class _RowsResult:
    """Minimal database result carrying a complete result population."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return all configured rows."""
        return self.rows


class _RowsConnection:
    """Record the provenance query and return configured database rows."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.query: str | None = None
        self.parameters: tuple[object, ...] | None = None

    def execute(self, query: str, parameters: tuple[object, ...]) -> _RowsResult:
        """Capture one query invocation and return configured rows."""
        self.query = query
        self.parameters = parameters
        return _RowsResult(self.rows)


class ReconciliationClosePackageAuthoritativeRunLoaderTests(unittest.TestCase):
    """Require exact run-command and retained-artifact provenance from PostgreSQL."""

    tenant_id = "tenant-id"
    tenant_reference = "tenant-acme"
    run_reference = "run-001"
    command_digest = "sha256:" + "1" * 64
    artifact_digest = "sha256:" + "2" * 64
    artifact_reference = "artifact-store:bank-statement-001"
    cutoff = datetime(2026, 8, 31, 0, 0, 0, 123456, tzinfo=timezone.utc)

    def _row(
        self,
        *,
        command_source_hash: str | None = None,
        statement_source_hash: str | None = None,
        artifact_source_hash: str | None = None,
        command_source_reference: str | None = None,
        artifact_store_reference: str | None = None,
    ) -> tuple[object, ...]:
        return (
            self.cutoff,
            self.command_digest,
            command_source_hash or self.artifact_digest,
            command_source_reference or self.artifact_reference,
            statement_source_hash or self.artifact_digest,
            artifact_source_hash or self.artifact_digest,
            artifact_store_reference or self.artifact_reference,
        )

    def _load(self, rows: list[tuple[object, ...]]):
        connection = _RowsConnection(rows)
        evidence = close_package._database_owned_run_source_evidence(
            connection,
            self.tenant_id,
            tenant_reference=self.tenant_reference,
            reconciliation_run_reference=self.run_reference,
        )
        return connection, evidence

    def test_loader_returns_canonical_fractional_cutoff_and_exact_source_evidence(self) -> None:
        connection, evidence = self._load([self._row()])
        self.assertEqual(
            connection.parameters,
            (self.tenant_id, self.run_reference),
        )
        self.assertIn("reconciliation_run_command", connection.query or "")
        self.assertIn("bank_statement_artifact", connection.query or "")
        run_evidence, artifact_evidence = evidence
        self.assertEqual(run_evidence.evidence_kind_code, "reconciliation_run")
        self.assertEqual(run_evidence.evidence_reference, self.run_reference)
        self.assertEqual(run_evidence.sha256_digest, self.command_digest)
        self.assertEqual(run_evidence.knowledge_cutoff, "2026-08-31T00:00:00.123456Z")
        self.assertEqual(artifact_evidence.evidence_kind_code, "statement_artifact")
        self.assertEqual(artifact_evidence.evidence_reference, self.artifact_reference)
        self.assertEqual(artifact_evidence.sha256_digest, self.artifact_digest)

    def test_loader_rejects_missing_run_command_or_artifact(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one run command and statement artifact"):
            self._load([])

    def test_loader_rejects_duplicate_run_command_or_artifact_population(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one run command and statement artifact"):
            self._load([self._row(), self._row()])

    def test_loader_rejects_command_and_statement_hash_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "run command source hash"):
            self._load([self._row(statement_source_hash="sha256:" + "3" * 64)])

    def test_loader_rejects_statement_and_artifact_hash_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "retained statement artifact hash"):
            self._load([self._row(artifact_source_hash="sha256:" + "4" * 64)])

    def test_loader_rejects_command_and_artifact_reference_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "retained statement artifact reference"):
            self._load([self._row(artifact_store_reference="artifact-store:other")])


if __name__ == "__main__":
    unittest.main()
