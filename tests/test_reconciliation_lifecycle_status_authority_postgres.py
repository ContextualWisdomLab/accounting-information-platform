"""Real PostgreSQL regression for reconciliation-run status authority."""

from __future__ import annotations

import unittest
from uuid import UUID

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleStatusAuthorityPostgresTests(unittest.TestCase):
    """Reject raw status writes that lack a named lifecycle command and evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Provision the real PostgreSQL foundation used by accounting acceptance tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one ordinary evaluating run whose status must remain command-owned."""
        self.fixture = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )

    def test_raw_non_reconciled_status_change_requires_named_lifecycle_command(self) -> None:
        """A privileged SQL session cannot manufacture another lifecycle state directly."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(psycopg.Error, "named lifecycle command"):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_run
                    SET run_status_code = 'not_reconciled'
                    WHERE reconciliation_run_id = %s
                    """,
                    (self.opened["reconciliation_run_id"],),
                )
            connection.rollback()

    def test_raw_terminal_run_insert_must_start_in_evaluating(self) -> None:
        """A privileged SQL session cannot create a pre-reconciled aggregate directly."""
        forged_run_id = UUID("44444444-4444-4444-8444-444444444444")
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(psycopg.Error, "must begin in evaluating"):
                connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_run (
                        reconciliation_run_id,
                        tenant_account_id,
                        legal_entity_id,
                        accounting_book_id,
                        bank_account_assignment_id,
                        currency_code,
                        bank_cutoff_at,
                        book_cutoff_at,
                        matching_policy_version,
                        knowledge_cutoff_at,
                        run_status_code
                    )
                    SELECT
                        %s,
                        tenant_account_id,
                        legal_entity_id,
                        accounting_book_id,
                        bank_account_assignment_id,
                        currency_code,
                        bank_cutoff_at,
                        book_cutoff_at,
                        matching_policy_version,
                        knowledge_cutoff_at,
                        'reconciled'
                    FROM accounting_core.reconciliation_run
                    WHERE reconciliation_run_id = %s
                    """,
                    (forged_run_id, self.opened["reconciliation_run_id"]),
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
