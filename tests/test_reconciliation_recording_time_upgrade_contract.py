"""Migration contracts for database-owned reconciliation control recording time."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = (
    _ROOT / "database/migrations/0026_reconciliation_control_recording_time_authority.sql"
)


class ReconciliationRecordingTimeUpgradeContractTests(unittest.TestCase):
    """Keep pre-0026 caller-shaped system time explicit without destroying history."""

    def test_legacy_rows_remain_untrusted_while_new_rows_gain_database_authority(self) -> None:
        """Upgrade must preserve old rows, tag provenance, and gate new command authority."""
        migration = _MIGRATION.read_text(encoding="utf-8")

        self.assertGreaterEqual(
            migration.count("ADD COLUMN recording_time_authority_code"),
            2,
        )
        self.assertGreaterEqual(
            migration.count("DEFAULT 'legacy_unverified'"),
            2,
        )
        self.assertGreaterEqual(
            migration.count("ALTER COLUMN recording_time_authority_code DROP DEFAULT"),
            2,
        )
        self.assertIn(
            "NEW.recording_time_authority_code := 'database_clock';",
            migration,
        )
        self.assertIn(
            "accounting_core.reject_reconciliation_control_recording_time_mutation",
            migration,
        )
        self.assertIn(
            "accounting_core.require_reconciliation_exception_resolution_recording_time_authority",
            migration,
        )
        self.assertIn(
            "reconciliation_resolution_recording_time_authority_required",
            migration,
        )
        self.assertNotIn("reconciliation_recording_time_legacy_preflight", migration)

        policy = "CREATE POLICY reconciliation_resolution_recording_time_upgrade_visibility"
        marker = "reconciliation_resolution_legacy_recording_time_preflight"
        drop_policy = "DROP POLICY reconciliation_resolution_recording_time_upgrade_visibility"
        first_durable_change = "ALTER TABLE accounting_core.reconciliation_exception"
        self.assertIn(policy, migration)
        self.assertIn("FOR SELECT\n    TO current_user\n    USING (true);", migration)
        self.assertIn(drop_policy, migration)
        self.assertLess(migration.index(policy), migration.index(marker))
        self.assertLess(migration.index(marker), migration.index(drop_policy))
        self.assertLess(migration.index(drop_policy), migration.index(first_durable_change))


if __name__ == "__main__":
    unittest.main()
