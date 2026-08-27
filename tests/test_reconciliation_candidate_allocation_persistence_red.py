"""RED contracts for durable reconciliation candidate/match allocation conservation."""

from __future__ import annotations

import re
import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg

from tests import test_postgres_posting as posting
from accounting_information_platform import (
    accept_bank_account_assignment,
    accept_bank_account_record,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0014_reconciliation_candidate_allocation.sql"
VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ReconciliationAllocationMigrationRedTests(unittest.TestCase):
    """Require normalized candidate/match/allocation evidence before persistence."""

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

    def test_single_approved_match_guard_is_declared(self) -> None:
        """One active approved reconciliation per run must be enforced relationally."""
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())
        self.assertIn("reconciliation_match_approved_single", normalized)
        self.assertIn("where match_status_code = 'approved'", normalized)


@unittest.skipUnless(
    MIGRATION.exists(), "RED until durable reconciliation allocation migration exists"
)
class PostgresReconciliationAllocationRedTests(unittest.TestCase):
    """Prove candidate/match schema and single-approval guard in PostgreSQL."""

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
                "account_identifier": "acct-opaque-allocation-fixture",
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
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
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
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
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
            connection.execute("RESET app.tenant_account_id")

    def _insert_candidate(self, statement_reference: str, journal_reference: str) -> str:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    reconciliation_candidate_id, tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference, statement_amount,
                    journal_amount, rule_code
                )
                VALUES (%s, %s, %s, %s, %s, '1000.00', '1000.00', 'provider_reference')
                RETURNING reconciliation_candidate_id
                """,
                (
                    uuid.uuid4(),
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    statement_reference,
                    journal_reference,
                ),
            ).fetchone()
        return row[0]

    def _approve_match(self, candidate_id: str) -> str:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,
                    reconciliation_candidate_id, match_status_code, approved_at
                )
                VALUES (%s, %s, %s, %s, 'approved', clock_timestamp())
                RETURNING reconciliation_match_id
                """,
                (
                    uuid.uuid4(),
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    candidate_id,
                ),
            ).fetchone()
        return row[0]

    def test_allocation_tables_enforce_tenant_scope_and_rows(self) -> None:
        """Candidate and allocation rows remain tenant-scoped with exact money."""
        candidate_id = self._insert_candidate("stmt-001", "journal-a")
        match_id = self._approve_match(candidate_id)
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
        self.assertEqual(rels[1][1], True)
        self.assertTrue(all(row[1] for row in rels))

    def test_second_approved_match_on_same_run_fails_closed(self) -> None:
        """At most one approved match per run is enforced by the partial unique index."""
        first_candidate = self._insert_candidate("stmt-001", "journal-a")
        self._approve_match(first_candidate)
        second_candidate = self._insert_candidate("stmt-002", "journal-b")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._approve_match(second_candidate)


if __name__ == "__main__":
    unittest.main()