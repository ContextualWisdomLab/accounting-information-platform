"""RED contracts for database-owned reconciliation approval state transitions."""

from __future__ import annotations

import uuid
import unittest

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_approval_evidence_red as approval
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


class PostgresReconciliationApprovalStateMachineRedTests(unittest.TestCase):
    """Require reviewed terminal match states to be controlled by durable evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        approval.PostgresReconciliationApprovalRedTests.setUpClass()

    def setUp(self) -> None:
        self.case = approval.PostgresReconciliationApprovalRedTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _insert_approval(self, match_id: object, decision: str) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_approval (
                    reconciliation_approval_id, tenant_account_id, reconciliation_run_id,
                    reconciliation_match_id, approval_command_key, source_payload_hash,
                    approver_reference, approval_purpose_code, approval_decision_code,
                    effective_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'controller-state-machine',
                        'reconciliation_review', %s, %s)
                """,
                (
                    uuid.uuid4(),
                    self.case.case.scope["tenant_account_id"],
                    self.case.case.run_reference,
                    match_id,
                    f"{decision}-{match_id}",
                    f"sha256:{match_id.hex}{match_id.hex}",
                    decision,
                    allocation.VALID_FROM,
                ),
            )

    def _set_status(self, match_id: object, status: str) -> None:
        approved_at_sql = "clock_timestamp()" if status == "approved" else "NULL"
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                f"""
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = %s, approved_at = {approved_at_sql}
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (
                    status,
                    self.case.case.scope["tenant_account_id"],
                    self.case.case.run_reference,
                    match_id,
                ),
            )

    def _assert_terminal_supersession_retains_approval(self, decision: str) -> None:
        _candidate_id, match_id = self.case._proposed_match()
        self._insert_approval(match_id, decision)
        self._set_status(match_id, decision)
        self._set_status(match_id, "superseded")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            status = connection.execute(
                """
                SELECT match_status_code
                FROM accounting_core.reconciliation_match
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (
                    self.case.case.scope["tenant_account_id"],
                    self.case.case.run_reference,
                    match_id,
                ),
            ).fetchone()[0]
            approval_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.reconciliation_approval
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                  AND approval_decision_code = %s
                """,
                (
                    self.case.case.scope["tenant_account_id"],
                    self.case.case.run_reference,
                    match_id,
                    decision,
                ),
            ).fetchone()[0]
        self.assertEqual(status, "superseded")
        self.assertEqual(approval_count, 1)

    def test_status_only_rejection_fails_closed(self) -> None:
        """A proposed match cannot become rejected without durable rejected evidence."""
        _candidate_id, match_id = self.case._proposed_match()
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._set_status(match_id, "rejected")

    def test_durable_rejected_approval_enables_exact_rejected_transition(self) -> None:
        """One immutable rejected decision authorizes exactly the rejected terminal state."""
        _candidate_id, match_id = self.case._proposed_match()
        self._insert_approval(match_id, "rejected")
        self._set_status(match_id, "rejected")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            status = connection.execute(
                """
                SELECT match_status_code
                FROM accounting_core.reconciliation_match
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (
                    self.case.case.scope["tenant_account_id"],
                    self.case.case.run_reference,
                    match_id,
                ),
            ).fetchone()[0]
        self.assertEqual(status, "rejected")

    def test_approved_terminal_match_cannot_reopen_to_proposed(self) -> None:
        """A reviewed approved decision is terminal unless explicitly superseded."""
        _candidate_id, match_id = self.case._proposed_match()
        self._insert_approval(match_id, "approved")
        self._set_status(match_id, "approved")

        with self.assertRaises(psycopg.errors.CheckViolation):
            self._set_status(match_id, "proposed")

    def test_rejected_terminal_match_cannot_reopen_to_proposed(self) -> None:
        """A reviewed rejected decision is terminal unless explicitly superseded."""
        _candidate_id, match_id = self.case._proposed_match()
        self._insert_approval(match_id, "rejected")
        self._set_status(match_id, "rejected")

        with self.assertRaises(psycopg.errors.CheckViolation):
            self._set_status(match_id, "proposed")

    def test_approved_terminal_match_may_be_superseded_without_rewriting_evidence(self) -> None:
        """Approved evidence remains immutable when its reviewed match is superseded."""
        self._assert_terminal_supersession_retains_approval("approved")

    def test_rejected_terminal_match_may_be_superseded_without_rewriting_evidence(self) -> None:
        """Rejected evidence remains immutable when its reviewed match is superseded."""
        self._assert_terminal_supersession_retains_approval("rejected")


if __name__ == "__main__":
    unittest.main()
