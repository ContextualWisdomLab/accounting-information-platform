"""RED/GREEN contracts for evidence-backed reconciliation-run completion.

A run may become ``reconciled`` only through a tenant-scoped, idempotent command
whose outcome is derived from persisted reconciliation evidence. Direct status
updates are not an accounting owner-control path.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import psycopg

from accounting_information_platform import accept_reconciliation_run_completion
from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_reconciliation_run_completion_evidence.sql"


class ReconciliationRunCompletionContractTests(unittest.TestCase):
    """Require a dedicated completion command instead of a generic status setter."""

    def test_completion_migration_and_public_command_exist(self) -> None:
        """The lifecycle repair must be explicit in schema and application code."""
        self.assertTrue(MIGRATION.exists())
        self.assertTrue(callable(accept_reconciliation_run_completion))


@unittest.skipUnless(
    MIGRATION.exists(),
    "RED until reconciliation-run completion evidence is installed",
)
class PostgresReconciliationRunCompletionRedTests(unittest.TestCase):
    """Prove PostgreSQL rejects unaudited or incomplete completion attempts."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            exists = connection.execute(
                "SELECT to_regclass('accounting_core.reconciliation_run_completion_command')"
            ).fetchone()[0]
            if exists is None:
                connection.execute(MIGRATION.read_text(encoding="utf-8"))

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

    def test_completion_rejects_run_without_approved_review_evidence(self) -> None:
        """Opening a run is not enough to manufacture reconciled accounting evidence."""
        with self.assertRaisesRegex(Exception, "approved reconciliation match"):
            accept_reconciliation_run_completion(
                {
                    "tenant_reference": self.fixture.case.policy.tenant_reference,
                    "reconciliation_run_id": str(self.fixture.run_reference),
                    "completion_idempotency_key": "completion-missing-review",
                },
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )


if __name__ == "__main__":
    unittest.main()
