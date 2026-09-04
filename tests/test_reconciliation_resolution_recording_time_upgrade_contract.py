"""Upgrade contracts for pre-0024 resolution recording-time authority."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "database/migrations/0024_reconciliation_control_recording_time_authority.sql"


class ReconciliationResolutionRecordingTimeUpgradeContractTests(unittest.TestCase):
    """Do not grandfather commands backed by unverifiable source chronology."""

    def test_upgrade_refuses_preexisting_resolution_commands_before_schema_change(self) -> None:
        """Authority-bearing pre-0024 commands require audited remediation."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        policy = "reconciliation_resolution_recording_time_upgrade_visibility"
        marker = "reconciliation_resolution_legacy_recording_time_preflight"
        first_schema_change = "ALTER TABLE accounting_core.reconciliation_exception"

        self.assertIn(f"CREATE POLICY {policy}", migration)
        self.assertIn(
            "ON accounting_core.reconciliation_exception_resolution_command",
            migration,
        )
        self.assertIn("FOR SELECT", migration)
        self.assertIn("TO current_user", migration)
        self.assertIn("USING (true)", migration)
        self.assertIn(marker, migration)
        self.assertIn(f"DROP POLICY {policy}", migration)
        self.assertLess(migration.index(policy), migration.index(marker))
        self.assertLess(migration.index(marker), migration.index(f"DROP POLICY {policy}"))
        self.assertLess(
            migration.index(f"DROP POLICY {policy}"),
            migration.index(first_schema_change),
        )


if __name__ == "__main__":
    unittest.main()
