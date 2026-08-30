"""RED PostgreSQL contracts for connected reviewed reconciliation candidate graphs."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


class PostgresReconciliationCandidateGraphConnectivityRedTests(unittest.TestCase):
    """Require one reviewed match to be one connected candidate-proposed component."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def _allocate_statements(
        self, match_id: uuid.UUID, allocations: tuple[tuple[str, str], ...]
    ) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            for statement_reference, amount in allocations:
                connection.execute(
                    """
                    INSERT INTO accounting_core.statement_match_allocation (
                        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                        statement_entry_reference, allocated_amount
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        self.fixture.scope["tenant_account_id"],
                        self.fixture.run_reference,
                        match_id,
                        statement_reference,
                        amount,
                    ),
                )

    def _allocate_journals(
        self, match_id: uuid.UUID, allocations: tuple[tuple[str, str], ...]
    ) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            for journal_reference, amount in allocations:
                connection.execute(
                    """
                    INSERT INTO accounting_core.journal_match_allocation (
                        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                        journal_reference, allocated_amount
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        self.fixture.scope["tenant_account_id"],
                        self.fixture.run_reference,
                        match_id,
                        journal_reference,
                        amount,
                    ),
                )

    def _record_approved_evidence(self, match_id: uuid.UUID) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_approval (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    approval_command_key, source_payload_hash, source_payload_reference,
                    approver_reference, approval_purpose_code, approval_decision_code,
                    effective_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'connectivity-reviewer',
                          'reconciliation_review', 'approved', %s)
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    match_id,
                    f"approve-connectivity-{match_id}",
                    "sha256:" + "7" * 64,
                    f"urn:cwl:object:approval-connectivity:{match_id}",
                    allocation.VALID_FROM,
                ),
            )

    def _finalize_approval(self, match_id: uuid.UUID) -> None:
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

    def test_approval_insert_rejects_disconnected_candidate_components(self) -> None:
        """Immutable approval evidence cannot bind an unrelated candidate component."""
        anchor = self.fixture._insert_candidate(
            "stmt-component-a",
            "journal-component-x",
            statement_amount="50.00",
            journal_amount="50.00",
        )
        self.fixture._insert_candidate(
            "stmt-component-b",
            "journal-component-y",
            statement_amount="50.00",
            journal_amount="50.00",
        )
        match_id = self.fixture._insert_match(anchor)
        self._allocate_statements(
            match_id,
            (("stmt-component-a", "50.00"), ("stmt-component-b", "50.00")),
        )
        self._allocate_journals(
            match_id,
            (("journal-component-x", "50.00"), ("journal-component-y", "50.00")),
        )

        with self.assertRaisesRegex(
            psycopg.errors.CheckViolation,
            "reconciliation_allocation_unproposed_pairing",
        ):
            self._record_approved_evidence(match_id)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            approval_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.reconciliation_approval
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
        self.assertEqual(approval_count, 0)

    def test_connected_split_candidate_graph_remains_approvable(self) -> None:
        """One statement split over candidate-proposed journals remains a valid review."""
        anchor = self.fixture._insert_candidate(
            "stmt-split",
            "journal-split-x",
            statement_amount="100.00",
            journal_amount="40.00",
        )
        self.fixture._insert_candidate(
            "stmt-split",
            "journal-split-y",
            statement_amount="100.00",
            journal_amount="60.00",
        )
        match_id = self.fixture._insert_match(anchor)
        self._allocate_statements(match_id, (("stmt-split", "100.00"),))
        self._allocate_journals(
            match_id, (("journal-split-x", "40.00"), ("journal-split-y", "60.00"))
        )

        self._record_approved_evidence(match_id)
        self._finalize_approval(match_id)

    def test_connected_aggregate_candidate_graph_remains_approvable(self) -> None:
        """Candidate-proposed statements aggregated into one journal remain valid."""
        anchor = self.fixture._insert_candidate(
            "stmt-aggregate-a",
            "journal-aggregate",
            statement_amount="40.00",
            journal_amount="100.00",
        )
        self.fixture._insert_candidate(
            "stmt-aggregate-b",
            "journal-aggregate",
            statement_amount="60.00",
            journal_amount="100.00",
        )
        match_id = self.fixture._insert_match(anchor)
        self._allocate_statements(
            match_id,
            (("stmt-aggregate-a", "40.00"), ("stmt-aggregate-b", "60.00")),
        )
        self._allocate_journals(match_id, (("journal-aggregate", "100.00"),))

        self._record_approved_evidence(match_id)
        self._finalize_approval(match_id)

    def test_connected_many_to_many_candidate_graph_remains_approvable(self) -> None:
        """A genuinely connected many-to-many candidate graph remains reviewable."""
        anchor = self.fixture._insert_candidate(
            "stmt-many-a",
            "journal-many-x",
            statement_amount="60.00",
            journal_amount="30.00",
        )
        self.fixture._insert_candidate(
            "stmt-many-a",
            "journal-many-y",
            statement_amount="60.00",
            journal_amount="70.00",
        )
        self.fixture._insert_candidate(
            "stmt-many-b",
            "journal-many-y",
            statement_amount="40.00",
            journal_amount="70.00",
        )
        match_id = self.fixture._insert_match(anchor)
        self._allocate_statements(
            match_id, (("stmt-many-a", "60.00"), ("stmt-many-b", "40.00"))
        )
        self._allocate_journals(
            match_id, (("journal-many-x", "30.00"), ("journal-many-y", "70.00"))
        )

        self._record_approved_evidence(match_id)
        self._finalize_approval(match_id)


if __name__ == "__main__":
    unittest.main()
