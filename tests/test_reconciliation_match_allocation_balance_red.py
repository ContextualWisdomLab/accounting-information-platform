"""RED PostgreSQL contracts for balanced reconciliation-match approval."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import (
    accept_bank_account_assignment,
    accept_bank_account_record,
)
from tests import test_postgres_posting as posting


VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PostgresReconciliationMatchAllocationBalanceRedTests(unittest.TestCase):
    """Require approved matches to carry non-empty, exactly balanced allocations."""

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
                "account_identifier": "acct-opaque-balance-fixture",
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
                "assignment_idempotency_key": f"assign-balance-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            assignment = connection.execute(
                """
                SELECT a.tenant_account_id, a.legal_entity_id, a.accounting_book_id,
                       a.bank_account_assignment_id
                FROM accounting_core.bank_account_assignment AS a
                JOIN accounting_core.tenant_account AS t
                  ON t.tenant_account_id = a.tenant_account_id
                WHERE t.tenant_account_code = %s
                ORDER BY a.recorded_at DESC
                LIMIT 1
                """,
                (self.case.policy.tenant_reference,),
            ).fetchone()
        self.scope = {
            "tenant_account_id": assignment[0],
            "legal_entity_id": assignment[1],
            "accounting_book_id": assignment[2],
            "bank_account_assignment_id": assignment[3],
        }
        self.run_reference = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
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

    def _create_proposed_match(self) -> uuid.UUID:
        candidate_id = uuid.uuid4()
        match_id = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    reconciliation_candidate_id, tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference, statement_amount,
                    journal_amount, rule_code
                )
                VALUES (%s, %s, %s, 'stmt-balance', 'journal-balance',
                        1000.000000, 1000.000000, 'provider_reference')
                """,
                (candidate_id, self.scope["tenant_account_id"], self.run_reference),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,
                    reconciliation_candidate_id, match_status_code, approved_at
                )
                VALUES (%s, %s, %s, %s, 'proposed', NULL)
                """,
                (
                    match_id,
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    candidate_id,
                ),
            )
        return match_id

    def _insert_statement_allocation(self, match_id: uuid.UUID, amount: str) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.statement_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    statement_entry_reference, allocated_amount
                )
                VALUES (%s, %s, %s, 'stmt-balance', %s)
                """,
                (
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    match_id,
                    amount,
                ),
            )

    def _insert_journal_allocation(self, match_id: uuid.UUID, amount: str) -> None:
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.journal_match_allocation (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    journal_reference, allocated_amount
                )
                VALUES (%s, %s, %s, 'journal-balance', %s)
                """,
                (
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    match_id,
                    amount,
                ),
            )

    def _record_approval_if_supported(self, match_id: uuid.UUID) -> None:
        """Keep this balance regression valid after durable approval evidence lands."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            approval_table = connection.execute(
                "SELECT to_regclass('accounting_core.reconciliation_approval')"
            ).fetchone()[0]
            if approval_table is None:
                return
            payload_hash = hashlib.sha256(str(match_id).encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_approval (
                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,
                    approval_command_key, source_payload_hash, source_payload_reference,
                    approver_reference,
                    approval_purpose_code, approval_decision_code, effective_at
                )
                VALUES (%s, %s, %s, %s, %s, 'urn:cwl:object:approval-command', 'balance-reviewer',
                        'reconciliation_review', 'approved', %s)
                """,
                (
                    self.scope["tenant_account_id"],
                    self.run_reference,
                    match_id,
                    f"approve-balance-{match_id}",
                    f"sha256:{payload_hash}",
                    VALID_FROM,
                ),
            )

    def _approve(self, match_id: uuid.UUID) -> None:
        self._record_approval_if_supported(match_id)
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

    def test_approval_rejects_unequal_statement_and_journal_totals(self) -> None:
        """Approved evidence cannot consume unequal amounts on the two source sides."""
        match_id = self._create_proposed_match()
        self._insert_statement_allocation(match_id, "1000.000000")
        self._insert_journal_allocation(match_id, "999.990000")

        with self.assertRaises(psycopg.errors.CheckViolation) as raised:
            self._approve(match_id)
        self.assertIn("reconciliation_match_unbalanced", str(raised.exception))

    def test_approval_rejects_a_missing_allocation_side(self) -> None:
        """An approved match must carry non-empty statement and journal allocation evidence."""
        match_id = self._create_proposed_match()
        self._insert_statement_allocation(match_id, "1000.000000")

        with self.assertRaises(psycopg.errors.CheckViolation) as raised:
            self._approve(match_id)
        self.assertIn("reconciliation_match_unbalanced", str(raised.exception))

    def test_approval_accepts_equal_non_empty_allocation_totals(self) -> None:
        """A balanced match remains approvable when both exact allocation sides tie."""
        match_id = self._create_proposed_match()
        self._insert_statement_allocation(match_id, "1000.000000")
        self._insert_journal_allocation(match_id, "1000.000000")
        self._approve(match_id)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            status = connection.execute(
                """
                SELECT match_status_code
                FROM accounting_core.reconciliation_match
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                  AND reconciliation_match_id = %s
                """,
                (self.scope["tenant_account_id"], self.run_reference, match_id),
            ).fetchone()[0]
        self.assertEqual(status, "approved")


if __name__ == "__main__":
    unittest.main()
