"""Repository contracts for reconciliation evidence aggregate membership."""

from __future__ import annotations

import unittest
from pathlib import Path


_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "database"
    / "migrations"
    / "0019_reconciliation_run_command_evidence.sql"
)


class ReconciliationEvidenceAggregateMembershipContractTests(unittest.TestCase):
    """Prevent reviewed evidence from escaping a finalized run by FK reassignment."""

    def test_lifecycle_guard_rejects_cross_aggregate_reassignment(self) -> None:
        """Tenant/run membership must be immutable before lifecycle-lock selection."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        function = migration.split(
            "CREATE OR REPLACE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation()",
            1,
        )[1].split("$$;", 1)[0]

        membership_guard = "NEW.reconciliation_run_id IS DISTINCT FROM OLD.reconciliation_run_id"
        lock_call = "PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock"
        self.assertIn("NEW.tenant_account_id IS DISTINCT FROM OLD.tenant_account_id", function)
        self.assertIn(membership_guard, function)
        self.assertIn("reconciliation_lifecycle_scope_immutable", function)
        self.assertLess(function.index(membership_guard), function.index(lock_call))


if __name__ == "__main__":
    unittest.main()
