"""RED tests for database-owned close-package run and statement provenance."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationCloseReviewScope,
)


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


class _StatusAwareRowsConnection(_RowsConnection):
    """Return run status only when production SQL actually requests it."""

    def __init__(self, row: tuple[object, ...], run_status_code: str) -> None:
        super().__init__([row])
        self.row = row
        self.run_status_code = run_status_code

    def execute(self, query: str, parameters: tuple[object, ...]) -> _RowsResult:
        """Expose status in the database row only for a status-aware query."""
        self.query = query
        self.parameters = parameters
        if "run_record.run_status_code" in query:
            return _RowsResult(
                [
                    (
                        self.row[0],
                        self.run_status_code,
                        *self.row[1:],
                    )
                ]
            )
        return _RowsResult([self.row])


class ReconciliationClosePackageAuthoritativeRunLoaderTests(unittest.TestCase):
    """Require exact run-command, scope, and retained-artifact provenance from PostgreSQL."""

    tenant_id = "tenant-id"
    tenant_reference = "tenant-acme"
    run_reference = "run-001"
    command_digest = "sha256:" + "1" * 64
    artifact_digest = "sha256:" + "2" * 64
    artifact_reference = "artifact-store:bank-statement-001"
    legal_entity_reference = "entity-acme"
    accounting_book_reference = "primary-book"
    bank_account_assignment_reference = "11111111-1111-1111-1111-111111111111"
    currency_code = "KRW"
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
            self.legal_entity_reference,
            self.accounting_book_reference,
            self.bank_account_assignment_reference,
            self.currency_code,
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

    def test_loader_returns_canonical_fractional_cutoff_exact_source_and_scope(self) -> None:
        connection, evidence = self._load([self._row()])
        self.assertEqual(
            connection.parameters,
            (self.tenant_id, self.run_reference),
        )
        query = connection.query or ""
        self.assertIn("reconciliation_run_command", query)
        self.assertIn("bank_statement_artifact", query)
        self.assertIn("legal_entity_record", query)
        self.assertIn("accounting_book", query)
        self.assertIn("FOR UPDATE OF run_record", query)
        self.assertIn(
            "FOR SHARE OF run_command, statement_record, statement_artifact",
            query,
        )
        run_evidence, artifact_evidence, run_scope = evidence
        self.assertEqual(run_evidence.evidence_kind_code, "reconciliation_run")
        self.assertEqual(run_evidence.evidence_reference, self.run_reference)
        self.assertEqual(run_evidence.sha256_digest, self.command_digest)
        self.assertEqual(run_evidence.knowledge_cutoff, "2026-08-31T00:00:00.123456Z")
        self.assertEqual(artifact_evidence.evidence_kind_code, "statement_artifact")
        self.assertEqual(artifact_evidence.evidence_reference, self.artifact_reference)
        self.assertEqual(artifact_evidence.sha256_digest, self.artifact_digest)
        self.assertEqual(
            run_scope,
            ReconciliationCloseReviewScope(
                tenant_account_reference=self.tenant_reference,
                legal_entity_reference=self.legal_entity_reference,
                accounting_book_reference=self.accounting_book_reference,
                bank_account_assignment_reference=self.bank_account_assignment_reference,
                currency_code=self.currency_code,
            ),
        )

    def test_loader_rejects_non_reconciled_database_status(self) -> None:
        for run_status_code in (
            "evaluating",
            "review_required",
            "not_reconciled",
            "superseded",
        ):
            with self.subTest(run_status_code=run_status_code):
                connection = _StatusAwareRowsConnection(self._row(), run_status_code)
                with self.assertRaisesRegex(
                    ValueError,
                    "database-owned reconciliation run must be reconciled",
                ):
                    close_package._database_owned_run_source_evidence(
                        connection,
                        self.tenant_id,
                        tenant_reference=self.tenant_reference,
                        reconciliation_run_reference=self.run_reference,
                    )
                self.assertIn(
                    "run_record.run_status_code",
                    connection.query or "",
                )

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
