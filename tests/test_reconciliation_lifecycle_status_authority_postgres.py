"""Real PostgreSQL regression for reconciliation-run status authority."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleStatusAuthorityPostgresTests(unittest.TestCase):
    """Reject raw status edits that lack a named lifecycle command and evidence."""

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


if __name__ == "__main__":
    unittest.main()
