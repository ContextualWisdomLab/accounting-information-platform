"""RED contracts for evidence-backed reconciliation-run completion.

A run may become ``reconciled`` only through a tenant-scoped, idempotent command
whose outcome is derived from persisted reconciliation evidence. Direct status
updates are not an accounting owner-control path.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import psycopg

from accounting_information_platform import reconciliation_run
from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_reconciliation_run_completion_evidence.sql"


class ReconciliationRunCompletionContractTests(unittest.TestCase):
    """Require a dedicated completion command instead of a generic status setter."""

    def test_completion_migration_and_public_command_exist(self) -> None:
        """The lifecycle repair must be explicit in schema and application code."""
        self.assertTrue(
            MIGRATION.exists(),
            "Add migration 0020 with immutable completion evidence and a database status guard.",
        )
        completion = getattr(reconciliation_run, "accept_reconciliation_run_completion", None)
        self.assertTrue(
            callable(completion),
            "Expose an evidence-derived accept_reconciliation_run_completion command.",
        )


@unittest.skipUnless(
    MIGRATION.exists(),
    "RED until reconciliation-run completion evidence is installed",
)
class PostgresReconciliationRunCompletionRedTests(unittest.TestCase):
    """Prove PostgreSQL rejects an unaudited transition to reconciled."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def test_direct_status_update_cannot_claim_reconciled(self) -> None:
        """A caller cannot manufacture reconciliation truth with direct SQL."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.fixture.scope["tenant_account_id"]),),
            )
            with self.assertRaisesRegex(psycopg.Error, "completion command"):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_run
                    SET run_status_code = 'reconciled'
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                    """,
                    (self.fixture.scope["tenant_account_id"], self.fixture.run_reference),
                )


if __name__ == "__main__":
    unittest.main()
