"""PostgreSQL proof that close-package state evidence cannot reuse superseded approvals."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform.persistence import PostgresPostingLedger
from accounting_information_platform.reconciliation_close_package import (
    ReconciliationApprovalEvidence,
    _database_owned_match_state_evidence,
)
from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


class PostgresReconciliationClosePackageActiveStateTests(unittest.TestCase):
    """Bind packaged approvals to the authoritative current PostgreSQL match state."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def _approved_evidence(
        self,
        suffix: str = "close-state",
    ) -> tuple[object, ReconciliationApprovalEvidence]:
        statement_reference = f"stmt-{suffix}"
        journal_reference = f"journal-{suffix}"
        candidate_id = self.fixture._insert_candidate(
            statement_reference,
            journal_reference,
        )
        match_id = self.fixture._insert_match(candidate_id)
        self.fixture._insert_allocations(
            match_id,
            statement_reference,
            journal_reference,
            "1000.00",
        )
        self.fixture._approve_match(match_id)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT source_payload_hash, source_payload_reference, reconciliation_snapshot_hash
                FROM accounting_core.reconciliation_approval
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (self.fixture.scope["tenant_account_id"], self.fixture.run_reference, match_id),
            ).fetchone()
        approval = ReconciliationApprovalEvidence(
            tenant_account_reference=self.fixture.case.policy.tenant_reference,
            reconciliation_run_reference=str(self.fixture.run_reference),
            reconciliation_match_reference=str(match_id),
            approval_decision_code="approved",
            source_payload_hash=str(row[0]),
            reconciliation_snapshot_sha256=str(row[2]),
            evidence_reference=str(row[1]),
        )
        return match_id, approval

    def _load_population(
        self,
        approval_evidence: tuple[ReconciliationApprovalEvidence, ...],
    ):
        ledger = PostgresPostingLedger(
            posting.DATABASE_URL, self.fixture.case.policy.tenant_reference
        )
        with ledger._session() as connection:
            tenant_account_id = ledger._require_tenant(connection)
            return _database_owned_match_state_evidence(
                connection,
                tenant_account_id,
                tenant_reference=self.fixture.case.policy.tenant_reference,
                reconciliation_run_reference=str(self.fixture.run_reference),
                approval_evidence=approval_evidence,
            )

    def _load(self, approval: ReconciliationApprovalEvidence):
        return self._load_population((approval,))

    def test_superseded_match_cannot_be_repackaged_from_immutable_approval(self) -> None:
        match_id, approval = self._approved_evidence()
        state = self._load(approval)
        self.assertEqual(state[0].evidence_reference, f"{match_id}:approved")

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = 'superseded'
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (self.fixture.scope["tenant_account_id"], self.fixture.run_reference, match_id),
            )

        with self.assertRaisesRegex(ValueError, "database-owned match state"):
            self._load(approval)

    def test_packaged_approvals_must_cover_every_active_approved_match(self) -> None:
        first_match, first_approval = self._approved_evidence("population-first")
        second_match, second_approval = self._approved_evidence("population-second")

        with self.assertRaisesRegex(ValueError, "active approved match population"):
            self._load_population(())
        with self.assertRaisesRegex(ValueError, "active approved match population"):
            self._load_population((first_approval,))

        state = self._load_population((first_approval, second_approval))
        self.assertEqual(
            {evidence.evidence_reference for evidence in state},
            {f"{first_match}:approved", f"{second_match}:approved"},
        )


if __name__ == "__main__":
    unittest.main()
