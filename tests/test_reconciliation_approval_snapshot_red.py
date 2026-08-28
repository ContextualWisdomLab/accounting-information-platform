"""RED contracts for database-owned reconciliation approval snapshots."""

from __future__ import annotations

import re
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0016_reconciliation_approval_evidence.sql"


class ReconciliationApprovalSnapshotMigrationRedTests(unittest.TestCase):
    """Require approval evidence to bind to a database-owned review snapshot."""

    def test_migration_defines_database_owned_snapshot_binding(self) -> None:
        """Approval evidence must not rely on a caller-supplied state hash."""
        self.assertTrue(
            MIGRATION.exists(),
            "Add migration 0016 for durable reconciliation approval snapshot evidence.",
        )
        normalized = re.sub(r"\s+", " ", MIGRATION.read_text(encoding="utf-8").lower())

        for contract in (
            "reconciliation_snapshot_version",
            "reconciliation_snapshot_hash",
            "reconciliation_match_snapshot_hash",
            "reconciliation_match_snapshot_lock",
            "sha256(",
        ):
            self.assertIn(contract, normalized)

        self.assertIn(
            "reconciliation_snapshot_hash := accounting_core.reconciliation_match_snapshot_hash",
            normalized,
        )
        self.assertIn(
            "approval.reconciliation_snapshot_hash = accounting_core.reconciliation_match_snapshot_hash",
            normalized,
        )


@unittest.skipUnless(
    MIGRATION.exists(), "RED until durable reconciliation approval migration exists"
)
class PostgresReconciliationApprovalSnapshotRedTests(unittest.TestCase):
    """Prove approval evidence binds to immutable database-owned review state."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def _insert_approval(self, match_id: uuid.UUID) -> str:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_approval (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    approval_command_key, source_payload_hash,
                    reconciliation_snapshot_hash, approver_reference,
                    approval_purpose_code, approval_decision_code, effective_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'operator-1',
                        'bank-close-review', 'approved', %s)
                RETURNING reconciliation_snapshot_hash
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    match_id,
                    f"approval-{uuid.uuid4().hex}",
                    "sha256:" + "0" * 64,
                    "sha256:" + "f" * 64,
                    datetime.now(timezone.utc),
                ),
            ).fetchone()
        return row[0]

    def test_approval_snapshot_is_database_owned_and_late_allocations_fail_closed(
        self,
    ) -> None:
        """A valid command hash cannot authorize a changed proposed allocation set."""
        candidate_id = self.fixture._insert_candidate(
            "stmt-snapshot", "journal-snapshot"
        )
        match_id = self.fixture._insert_match(candidate_id)
        self.fixture._insert_allocations(
            match_id, "stmt-snapshot", "journal-snapshot", "1000.00"
        )

        stored_hash = self._insert_approval(match_id)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            expected_hash = connection.execute(
                """
                SELECT accounting_core.reconciliation_match_snapshot_hash(
                    %s, %s, %s
                )
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    match_id,
                ),
            ).fetchone()[0]
        self.assertEqual(stored_hash, expected_hash)
        self.assertNotEqual(stored_hash, "sha256:" + "f" * 64)

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_core.statement_match_allocation (
                        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                        statement_entry_reference, allocated_amount
                    )
                    VALUES (%s, %s, %s, 'stmt-snapshot', '1.00')
                    """,
                    (
                        self.fixture.scope["tenant_account_id"],
                        self.fixture.run_reference,
                        match_id,
                    ),
                )

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = 'approved', approved_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    match_id,
                ),
            )


if __name__ == "__main__":
    unittest.main()
