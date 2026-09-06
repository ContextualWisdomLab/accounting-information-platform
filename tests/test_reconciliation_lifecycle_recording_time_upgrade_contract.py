"""Upgrade contracts for reconciliation lifecycle recording-time authority."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "database/migrations/0026_reconciliation_lifecycle_recording_time_authority.sql"


class ReconciliationLifecycleRecordingTimeUpgradeContractTests(unittest.TestCase):
    """Prevent pre-0026 caller-shaped system time from becoming close authority."""

    def test_upgrade_refuses_unverifiable_legacy_transition_rows(self) -> None:
        """A pre-0026 transition cannot be silently promoted to database-clock authority."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        marker = "reconciliation_lifecycle_legacy_recording_time_preflight"
        visibility_policy = "reconciliation_lifecycle_recording_time_upgrade_visibility"
        self.assertIn(visibility_policy, migration)
        self.assertIn("FOR SELECT", migration)
        self.assertIn("TO current_user", migration)
        self.assertIn(marker, migration)
        self.assertIn("reconciliation_run_transition_command", migration)
        self.assertLess(migration.index(visibility_policy), migration.index(marker))
        self.assertLess(
            migration.index(marker),
            migration.index("ADD COLUMN recording_time_authority_code"),
        )

    def test_new_transition_time_is_explicit_database_clock_authority(self) -> None:
        """Post-upgrade transition evidence receives database-owned system time and provenance."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn("recording_time_authority_code", migration)
        self.assertIn("legacy_unverified", migration)
        self.assertIn("database_clock", migration)
        self.assertIn("NEW.recorded_at := clock_timestamp();", migration)
        self.assertIn("NEW.recording_time_authority_code := 'database_clock';", migration)
        self.assertIn("NEW.effective_at > NEW.recorded_at", migration)


if __name__ == "__main__":
    unittest.main()
