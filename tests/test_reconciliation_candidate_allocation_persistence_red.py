"""RED contracts for durable reconciliation candidate/match allocation conservation."""

from __future__ import annotations

import re
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting
from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_account_assignment,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0014_reconciliation_candidate_allocation.sql"
CONSERVATION_MIGRATION = ROOT / "database/migrations/0015_reconciliation_multi_match_conservation.sql"
VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ReconciliationAllocationMigrationRedTests(unittest.TestCase):
    """Require normalized candidate/match/allocation evidence and corrective conservation."""

    def test_migration_defines_candidate_match_and_allocation_rows(self) -> None:
        """Conservation tables are 3NF rows, tenant-scoped, with no JSON blobs."""
        self.assertTrue(
            MIGRATION.exists(),
            "Add migration 0014 for candidate/match/allocation evidence before persisting any run result.",
        )
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for object_name in (
            "reconciliation_candidate",
            "reconciliation_match",
            "statement_match_allocation",
            "journal_match_allocation",
        ):
            self.assertIn(f"create table accounting_core.{object_name}", normalized)

        for column_name in (
            "tenant_account_id",
            "reconciliation_run_id",
            "statement_entry_reference",
            "journal_reference",
            "allocated_amount",
            "reconciliation_candidate_id",
            "reconciliation_match_id",
            "match_status_code",
        ):
            self.assertIn(column_name, normalized)

        self.assertNotIn("jsonb", normalized)
        self.assertEqual(normalized.count("force row level security"), 4)

    def test_corrective_migration_replaces_run_wide_approval_guard_with_conservation(self) -> None:
        """A run may approve many independent matches while source amounts remain conserved."""
        self.assertTrue(
            CONSERVATION_MIGRATION.exists(),
            "Add migration 0015 to replace the run-wide approval index with source-allocation conservation.",
        )
        migration = CONSERVATION_MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())
        self.assertIn("drop index accounting_core.reconciliation_match_approved_single", normalized)
        self.assertIn("reconciliation_allocation_conservation_guard", normalized)
        self.assertIn("reconciliation_match_scope_foreign_key", normalized)
        self.assertIn("reconciliation_candidate_scope_foreign_key", normalized)
        self.assertIn("reconciliation_match_unbalanced", normalized)


@unittest.skipUnless(
    MIGRATION.exists(), "RED until durable reconciliation allocation migration exists"
)
class PostgresReconciliationAllocationRedTests(unittest.TestCase):
    """Prove multi-match conservation and tenant/run provenance in PostgreSQL."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-opaque-fixture-only",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        accept_bank_account_assignment(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
                "assignment_idempotency_key": f"assign-setup-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        statement_payload = load_canonical_statement_fixture().replace(
            b"Invoice 1001", f"Invoice {uuid.uuid4().hex[:8]}".encode(), 1
        )
        self.statement_record = accept_bank_statement_evidence(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": statement_payload.decode("utf-8"),
                "ingestion_idempotency_key": f"statement-run-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            assignments = connection.execute(
                """
                SELECT a.tenant_account_id, a.legal_entity_id, a.accounting_book_id,
                       a.bank_account_assignment_id
                FROM accounting_core.bank_account_assignment AS a
                JOIN accounting_core.tenant_account AS t ON t.tenant_account_id = a.tenant_account_id
                WHERE t.tenant_account_code = %s
                ORDER BY a.recorded_at DESC
                """,
                (self.case.policy.tenant_reference,),
            ).fetchall()
        self.scope = {
            "tenant_account_id": assignments[0][0],
            "legal_entity_id": assignments[0][1],
            "accounting_book_id": assignments[0][2],
            "bank_account_assignment_id": assignments[0][3],
        }
        self.run_reference = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.scope["tenant_account_id"]),),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_run (
                    reconciliation_run_id, tenant_account_id, legal_entity_id,
                    accounting_book_id, bank_account_assignment_id, currency_code,
                    bank_cutoff_at, book_cutoff_at, matching_policy_version,
                    knowledge_cutoff_at, run_status_code
                )
                VALUES (%s, %s, %s, %s, %s, 'KRW', %s, %s, 'policy-v1', %s, 'evaluating')
                """,
                (
                    self.run_reference,
                    self.scope["tenant_account_id"],
                    self.scope["legal_entity_id"],
                    self.scope["accounting_book_id"],
                    self.scope["bank_account_assignment_id"],
                    VALID_FROM,
                    VALID_FROM,
                    VALID_FROM,
                ),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_run_command (
                    tenant_account_id, reconciliation_run_id, bank_statement_record_id,
                    reconciliation_idempotency_key, reconciliation_command_hash,
                    source_payload_hash, source_payload_reference
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    self.statement_record["bank_statement_record_id"],
                    f"run-evidence-{uuid.uuid4().hex}",
                    "sha256:" + "c" * 64,
                    self.statement_record["source_artifact_hash"],
                    f"memory:{self.statement_record['source_artifact_hash']}",
                ),
            )
            connection.commit()

    def _insert_candidate(
        self,
        statement_reference: str,
        journal_reference: str,
        *,
        statement_amount: str = "1000.00",
        journal_amount: str = "1000.00",
    ) -> uuid.UUID:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    reconciliation_candidate_id, tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference, statement_amount,
                    journal_amount, rule_code
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'provider_reference')
                RETURNING reconciliation_candidate_id
                """,
                (
                    uuid.uuid4(),
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    statement_reference,
                    journal_reference,
                    statement_amount,
                    journal_amount,
                ),
            ).fetchone()
        return row[0]

    def _insert_match(self, candidate_id: uuid.UUID, status: str = "proposed") -> uuid.UUID:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,
                    reconciliation_candidate_id, match_status_code, approved_at
                )
                VALUES (%s, %s, %s, %s, %s,
                        CASE WHEN %s = 'approved' THEN clock_timestamp() ELSE NULL END)
                RETURNING reconciliation_match_id
                """,
                (
                    uuid.uuid4(),
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    candidate_id,
                    status,
                    status,
                ),
            ).fetchone()
        return row[0]

    def _approve_match(self, match_id: uuid.UUID) -> None:
        self._record_approval(match_id)
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_match
                SET match_status_code = 'approved', approved_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (self.scope["tenant_account_id"], self.run_reference, match_id),
            )

    def _record_approval(
        self,
        match_id: uuid.UUID,
        *,
        run_reference: uuid.UUID | None = None,
    ) -> None:
        """Record valid durable review evidence before a terminal match transition."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_approval (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    approval_command_key, source_payload_hash, source_payload_reference,
                    approver_reference,
                    approval_purpose_code, approval_decision_code, effective_at
                )
                VALUES (%s, %s, %s, %s, %s, 'urn:cwl:object:approval-command', 'test-reviewer',
                        'reconciliation_review', 'approved', %s)
                """,
                (
                    self.scope["tenant_account_id"],
                    run_reference or self.run_reference,
                    match_id,
                    f"approve-{match_id}",
                    "sha256:" + "0" * 64,
                    VALID_FROM,
                ),
            )

    def _insert_allocations(
        self,
        match_id: uuid.UUID,
        statement_reference: str,
        journal_reference: str,
        amount: str,
    ) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.statement_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    statement_entry_reference, allocated_amount
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    match_id,
                    statement_reference,
                    amount,
                ),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.journal_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    journal_reference, allocated_amount
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    match_id,
                    journal_reference,
                    amount,
                ),
            )

    def test_allocation_tables_enforce_tenant_scope_and_rows(self) -> None:
        """Candidate and allocation rows remain tenant-scoped with exact money."""
        candidate_id = self._insert_candidate("stmt-001", "journal-a")
        match_id = self._insert_match(candidate_id)
        self._insert_allocations(match_id, "stmt-001", "journal-a", "1000.00")
        self._approve_match(match_id)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT c.statement_entry_reference, m.match_status_code
                FROM accounting_core.reconciliation_candidate AS c
                JOIN accounting_core.reconciliation_match AS m
                  ON m.reconciliation_candidate_id = c.reconciliation_candidate_id
                WHERE c.tenant_account_id = %s
                ORDER BY c.statement_entry_reference
                """,
                (self.scope["tenant_account_id"],),
            ).fetchall()
        self.assertEqual(rows, [("stmt-001", "approved")])
        self.assertIsInstance(candidate_id, uuid.UUID)
        self.assertIsInstance(match_id, uuid.UUID)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rels = connection.execute(
                """
                SELECT c.relname, c.relforcerowsecurity
                FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'accounting_core'
                  AND c.relname = ANY(%s)
                ORDER BY c.relname
                """,
                (
                    [
                        "journal_match_allocation",
                        "reconciliation_candidate",
                        "reconciliation_match",
                        "statement_match_allocation",
                    ],
                ),
            ).fetchall()
        self.assertTrue(all(row[1] for row in rels))

    def test_two_independent_matches_can_be_approved_in_one_run(self) -> None:
        """A commercial reconciliation run may approve more than one disjoint safe match."""
        first_candidate = self._insert_candidate("stmt-001", "journal-a")
        first_match = self._insert_match(first_candidate)
        self._insert_allocations(first_match, "stmt-001", "journal-a", "1000.00")
        self._approve_match(first_match)

        second_candidate = self._insert_candidate("stmt-002", "journal-b")
        second_match = self._insert_match(second_candidate)
        self._insert_allocations(second_match, "stmt-002", "journal-b", "1000.00")
        self._approve_match(second_match)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.reconciliation_match
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND match_status_code = 'approved'
                """,
                (self.scope["tenant_account_id"], self.run_reference),
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_approved_matches_cannot_overconsume_one_statement(self) -> None:
        """Multiple approvals stay legal only while exact statement allocation is conserved."""
        first_candidate = self._insert_candidate(
            "stmt-shared", "journal-a", statement_amount="1000.00", journal_amount="600.00"
        )
        first_match = self._insert_match(first_candidate)
        self._insert_allocations(first_match, "stmt-shared", "journal-a", "600.00")
        self._approve_match(first_match)

        second_candidate = self._insert_candidate(
            "stmt-shared", "journal-b", statement_amount="1000.00", journal_amount="500.00"
        )
        second_match = self._insert_match(second_candidate)
        self._insert_allocations(second_match, "stmt-shared", "journal-b", "500.00")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._approve_match(second_match)


if __name__ == "__main__":
    unittest.main()
