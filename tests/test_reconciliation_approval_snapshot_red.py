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
            "source_payload_reference",
            "reconciliation_approval_upgrade_guard",
            "a_reconciliation_statement_allocation_snapshot_lock",
            "a_reconciliation_journal_allocation_snapshot_lock",
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

    def _insert_approval(self, match_id: uuid.UUID, decision_code: str = "approved") -> str:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_approval (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    approval_command_key, source_payload_hash,
                    source_payload_reference, reconciliation_snapshot_hash, approver_reference,
                    approval_purpose_code, approval_decision_code, effective_at
                )
                VALUES (%s, %s, %s, %s, %s, 'urn:cwl:object:approval-command', %s, 'operator-1',
                        'bank-close-review', %s, %s)
                RETURNING reconciliation_snapshot_hash
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    match_id,
                    f"approval-{uuid.uuid4().hex}",
                    "sha256:" + "0" * 64,
                    "sha256:" + "f" * 64,
                    decision_code,
                    datetime.now(timezone.utc),
                ),
            ).fetchone()
        return row[0]

    def test_approved_evidence_requires_complete_balanced_allocation_snapshot(self) -> None:
        """Approval evidence cannot freeze an empty population that can never be approved."""
        candidate_id = self.fixture._insert_candidate(
            "stmt-approval-order", "journal-approval-order"
        )
        match_id = self.fixture._insert_match(candidate_id)

        with self.assertRaisesRegex(
            psycopg.errors.CheckViolation,
            "reconciliation_match_unbalanced",
        ):
            self._insert_approval(match_id)

        self.fixture._insert_allocations(
            match_id, "stmt-approval-order", "journal-approval-order", "1000.00"
        )
        stored_hash = self._insert_approval(match_id)
        self.assertRegex(stored_hash, r"^sha256:[0-9a-f]{64}$")

    def test_rejected_evidence_can_snapshot_a_candidate_without_allocations(self) -> None:
        """Rejection remains possible before allocation because it consumes no source capacity."""
        candidate_id = self.fixture._insert_candidate(
            "stmt-rejected-empty", "journal-rejected-empty"
        )
        match_id = self.fixture._insert_match(candidate_id)

        stored_hash = self._insert_approval(match_id, "rejected")
        self.assertRegex(stored_hash, r"^sha256:[0-9a-f]{64}$")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = 'rejected'
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

    def test_approved_match_cannot_be_retargeted_to_another_candidate(self) -> None:
        """An approved match keeps the candidate identity that was reviewed."""
        candidate_id = self.fixture._insert_candidate(
            "stmt-retargeted", "journal-retargeted"
        )
        replacement_candidate_id = self.fixture._insert_candidate(
            "stmt-replacement", "journal-replacement"
        )
        match_id = self.fixture._insert_match(candidate_id)
        self.fixture._insert_allocations(
            match_id, "stmt-retargeted", "journal-retargeted", "1000.00"
        )
        self.fixture._approve_match(match_id)

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_match_identity_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET reconciliation_candidate_id = %s
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        replacement_candidate_id,
                        self.fixture.scope["tenant_account_id"],
                        self.fixture.run_reference,
                        match_id,
                    ),
                )

    def test_approval_evidence_freezes_candidate_before_terminal_transition(self) -> None:
        """A pending terminal transition cannot outlive a retargeted approval."""
        candidate_id = self.fixture._insert_candidate(
            "stmt-pending-retarget", "journal-pending-retarget"
        )
        replacement_candidate_id = self.fixture._insert_candidate(
            "stmt-pending-replacement", "journal-pending-replacement"
        )
        match_id = self.fixture._insert_match(candidate_id)
        self.fixture._insert_allocations(
            match_id, "stmt-pending-retarget", "journal-pending-retarget", "1000.00"
        )
        self._insert_approval(match_id)

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_match_identity_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET reconciliation_candidate_id = %s
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        replacement_candidate_id,
                        self.fixture.scope["tenant_account_id"],
                        self.fixture.run_reference,
                        match_id,
                    ),
                )

    def test_approved_match_preserves_approval_time_when_superseded(self) -> None:
        """Supersession retires a match without rewriting its approval timestamp."""
        candidate_id = self.fixture._insert_candidate(
            "stmt-supersede-time", "journal-supersede-time"
        )
        match_id = self.fixture._insert_match(candidate_id)
        self.fixture._insert_allocations(
            match_id, "stmt-supersede-time", "journal-supersede-time", "1000.00"
        )
        self.fixture._approve_match(match_id)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            approved_at = connection.execute(
                """
                SELECT approved_at
                FROM accounting_core.reconciliation_match
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    match_id,
                ),
            ).fetchone()[0]

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_review_terminal",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET match_status_code = 'superseded', approved_at = NULL
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

        with psycopg.connect(posting.DATABASE_URL) as connection:
            self.assertEqual(
                connection.execute(
                    """
                    SELECT match_status_code, approved_at
                    FROM accounting_core.reconciliation_match
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (
                        self.fixture.scope["tenant_account_id"],
                        self.fixture.run_reference,
                        match_id,
                    ),
                ).fetchone(),
                ("approved", approved_at),
            )


if __name__ == "__main__":
    unittest.main()
