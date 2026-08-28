"""RED regressions for reconciliation source identity and terminal review controls."""

from __future__ import annotations

import re
import unittest
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


ROOT = Path(__file__).resolve().parents[1]
CONSERVATION_MIGRATION = ROOT / "database/migrations/0015_reconciliation_multi_match_conservation.sql"
APPROVAL_MIGRATION = ROOT / "database/migrations/0016_reconciliation_approval_evidence.sql"


class ReconciliationDatabaseControlMigrationRedTests(unittest.TestCase):
    """Keep migration text explicit about non-vacuous database control boundaries."""

    def test_candidate_admission_serializes_on_stable_source_identity(self) -> None:
        """Candidate amount admission must lock the stable bank source before conflict reads."""
        normalized = re.sub(
            r"\s+", " ", CONSERVATION_MIGRATION.read_text(encoding="utf-8").lower()
        )
        self.assertIn("bank_account_record_id", normalized)
        self.assertIn("reconciliation-candidate-statement", normalized)
        self.assertIn("reconciliation-candidate-journal", normalized)
        self.assertIn("pg_advisory_xact_lock", normalized)

    def test_upgrade_guard_has_transactional_migration_visibility(self) -> None:
        """Forced RLS must not make the legacy reviewed-row upgrade check vacuous."""
        normalized = re.sub(
            r"\s+", " ", APPROVAL_MIGRATION.read_text(encoding="utf-8").lower()
        )
        self.assertIn("reconciliation_approval_upgrade_visibility", normalized)
        self.assertIn("to current_user", normalized)
        self.assertIn(
            "drop policy reconciliation_approval_upgrade_visibility on accounting_core.reconciliation_match",
            normalized,
        )


@unittest.skipUnless(
    APPROVAL_MIGRATION.exists(), "RED until reconciliation approval controls exist"
)
class PostgresReconciliationDatabaseControlHardeningRedTests(unittest.TestCase):
    """Prove reviewed evidence stays bound to stable source and terminal identities."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def test_match_cannot_retarget_candidate_after_allocations_exist(self) -> None:
        """Allocation evidence freezes the candidate identity before approval evidence exists."""
        first_candidate = self.fixture._insert_candidate("stmt-retarget-a", "journal-retarget-a")
        replacement_candidate = self.fixture._insert_candidate(
            "stmt-retarget-b", "journal-retarget-b"
        )
        match_id = self.fixture._insert_match(first_candidate)
        self.fixture._insert_allocations(
            match_id, "stmt-retarget-a", "journal-retarget-a", "1000.00"
        )

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
                        replacement_candidate,
                        self.fixture.scope["tenant_account_id"],
                        self.fixture.run_reference,
                        match_id,
                    ),
                )

    def test_superseded_match_cannot_reopen_or_rewrite_review_time(self) -> None:
        """Superseded review history is terminal and cannot reactivate old approval evidence."""
        candidate_id = self.fixture._insert_candidate(
            "stmt-terminal", "journal-terminal"
        )
        match_id = self.fixture._insert_match(candidate_id)
        self.fixture._insert_allocations(
            match_id, "stmt-terminal", "journal-terminal", "1000.00"
        )
        self.fixture._approve_match(match_id)
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = 'superseded'
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
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_review_terminal",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET match_status_code = 'approved'
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
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_review_terminal",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET approved_at = approved_at + interval '1 second'
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

    def test_conflicting_candidate_amount_cannot_enter_concurrently(self) -> None:
        """Two runs cannot concurrently establish different capacities for one source identity."""
        second_run = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_run (
                    reconciliation_run_id, tenant_account_id, legal_entity_id,
                    accounting_book_id, bank_account_assignment_id, currency_code,
                    bank_cutoff_at, book_cutoff_at, matching_policy_version,
                    knowledge_cutoff_at, run_status_code
                )
                SELECT %s, tenant_account_id, legal_entity_id, accounting_book_id,
                       bank_account_assignment_id, currency_code, bank_cutoff_at,
                       book_cutoff_at, matching_policy_version, knowledge_cutoff_at,
                       'evaluating'
                FROM accounting_core.reconciliation_run
                WHERE tenant_account_id = %s AND reconciliation_run_id = %s
                """,
                (
                    second_run,
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                ),
            )

        first_candidate = uuid.uuid4()
        second_candidate = uuid.uuid4()
        insert_sql = """
            INSERT INTO accounting_core.reconciliation_candidate (
                reconciliation_candidate_id, tenant_account_id, reconciliation_run_id,
                statement_entry_reference, journal_reference, statement_amount,
                journal_amount, rule_code
            ) VALUES (%s, %s, %s, 'stmt-concurrent-capacity', %s, %s, %s, 'provider_reference')
        """

        with psycopg.connect(posting.DATABASE_URL) as first:
            first.execute(
                insert_sql,
                (
                    first_candidate,
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    "journal-concurrent-a",
                    "1000.00",
                    "1000.00",
                ),
            )
            with psycopg.connect(posting.DATABASE_URL) as second:
                second.execute("SET LOCAL lock_timeout = '500ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    second.execute(
                        insert_sql,
                        (
                            second_candidate,
                            self.fixture.scope["tenant_account_id"],
                            second_run,
                            "journal-concurrent-b",
                            "1200.00",
                            "1200.00",
                        ),
                    )
            first.commit()

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_source_amount_conflict",
            ):
                connection.execute(
                    insert_sql,
                    (
                        second_candidate,
                        self.fixture.scope["tenant_account_id"],
                        second_run,
                        "journal-concurrent-b",
                        "1200.00",
                        "1200.00",
                    ),
                )

    def test_statement_capacity_survives_assignment_rollover_for_same_bank_account(self) -> None:
        """Effective-dated assignment replacement cannot reset immutable bank-source consumption."""
        first_candidate = self.fixture._insert_candidate(
            "stmt-stable-bank-source", "journal-stable-a"
        )
        first_match = self.fixture._insert_match(first_candidate)
        self.fixture._insert_allocations(
            first_match, "stmt-stable-bank-source", "journal-stable-a", "1000.00"
        )
        self.fixture._approve_match(first_match)

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            assignment = connection.execute(
                """
                SELECT bank_account_record_id, chart_account_id, valid_from
                FROM accounting_core.bank_account_assignment
                WHERE tenant_account_id = %s AND bank_account_assignment_id = %s
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.scope["bank_account_assignment_id"],
                ),
            ).fetchone()
            rollover_at = assignment[2] + timedelta(days=1)
            connection.execute(
                """
                UPDATE accounting_core.bank_account_assignment
                SET valid_to = %s
                WHERE tenant_account_id = %s AND bank_account_assignment_id = %s
                """,
                (
                    rollover_at,
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.scope["bank_account_assignment_id"],
                ),
            )
            second_assignment = connection.execute(
                """
                INSERT INTO accounting_core.bank_account_assignment (
                    tenant_account_id, bank_account_record_id, legal_entity_id,
                    accounting_book_id, chart_account_id, valid_from,
                    assignment_idempotency_key, assignment_command_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING bank_account_assignment_id
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    assignment[0],
                    self.fixture.scope["legal_entity_id"],
                    self.fixture.scope["accounting_book_id"],
                    assignment[1],
                    rollover_at,
                    f"rollover-{uuid.uuid4().hex}",
                    "sha256:" + "a" * 64,
                ),
            ).fetchone()[0]
            second_run = uuid.uuid4()
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_run (
                    reconciliation_run_id, tenant_account_id, legal_entity_id,
                    accounting_book_id, bank_account_assignment_id, currency_code,
                    bank_cutoff_at, book_cutoff_at, matching_policy_version,
                    knowledge_cutoff_at, run_status_code
                ) VALUES (%s, %s, %s, %s, %s, 'KRW', %s, %s, 'policy-v1', %s, 'evaluating')
                """,
                (
                    second_run,
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.scope["legal_entity_id"],
                    self.fixture.scope["accounting_book_id"],
                    second_assignment,
                    rollover_at,
                    rollover_at,
                    rollover_at,
                ),
            )
            second_candidate = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    reconciliation_candidate_id, tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference, statement_amount,
                    journal_amount, rule_code
                ) VALUES (%s, %s, %s, 'stmt-stable-bank-source', 'journal-stable-b',
                          '1000.00', '1000.00', 'provider_reference')
                RETURNING reconciliation_candidate_id
                """,
                (uuid.uuid4(), self.fixture.scope["tenant_account_id"], second_run),
            ).fetchone()[0]
            second_match = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,
                    reconciliation_candidate_id, match_status_code
                ) VALUES (%s, %s, %s, %s, 'proposed')
                RETURNING reconciliation_match_id
                """,
                (
                    uuid.uuid4(),
                    self.fixture.scope["tenant_account_id"],
                    second_run,
                    second_candidate,
                ),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.statement_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    statement_entry_reference, allocated_amount
                ) VALUES (%s, %s, %s, 'stmt-stable-bank-source', '1000.00')
                """,
                (self.fixture.scope["tenant_account_id"], second_run, second_match),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.journal_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    journal_reference, allocated_amount
                ) VALUES (%s, %s, %s, 'journal-stable-b', '1000.00')
                """,
                (self.fixture.scope["tenant_account_id"], second_run, second_match),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_approval (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    approval_command_key, source_payload_hash, source_payload_reference,
                    approver_reference, approval_purpose_code, approval_decision_code,
                    effective_at
                ) VALUES (%s, %s, %s, %s, %s, 'urn:cwl:object:rollover-approval',
                          'test-reviewer', 'reconciliation_review', 'approved', %s)
                """,
                (
                    self.fixture.scope["tenant_account_id"],
                    second_run,
                    second_match,
                    f"approve-{second_match}",
                    "sha256:" + "b" * 64,
                    rollover_at,
                ),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_allocation_overconsumed",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match
                    SET match_status_code = 'approved', approved_at = clock_timestamp()
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                      AND reconciliation_match_id = %s
                    """,
                    (self.fixture.scope["tenant_account_id"], second_run, second_match),
                )


if __name__ == "__main__":
    unittest.main()
