"""Repository contract for reconciliation-run initial lifecycle authority."""

from __future__ import annotations

import unittest
from pathlib import Path


_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "database"
    / "migrations"
    / "0019_reconciliation_run_command_evidence.sql"
)


class ReconciliationLifecycleInitialStateContractTests(unittest.TestCase):
    """Keep terminal run creation behind the named lifecycle state machine."""

    def test_new_runs_must_start_evaluating(self) -> None:
        """Migration 0019 must guard INSERT as well as later status updates."""
        migration = _MIGRATION.read_text(encoding="utf-8")

        self.assertIn("IF TG_OP = 'INSERT' THEN", migration)
        self.assertIn("NEW.run_status_code <> 'evaluating'", migration)
        self.assertIn("reconciliation_lifecycle_initial_state", migration)
        self.assertIn(
            "BEFORE INSERT OR UPDATE OF run_status_code ON accounting_core.reconciliation_run",
            migration,
        )


if __name__ == "__main__":
    unittest.main()
