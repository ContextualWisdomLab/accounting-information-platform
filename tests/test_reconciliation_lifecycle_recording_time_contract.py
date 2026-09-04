"""Contracts for database-owned reconciliation lifecycle system time."""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from accounting_information_platform import migration_install

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "database/migrations/0025_reconciliation_lifecycle_recording_time_authority.sql"


class ReconciliationLifecycleRecordingTimeContractTests(unittest.TestCase):
    """Keep lifecycle valid time subordinate to database-owned recording time."""

    def test_canonical_installer_requires_transition_recording_time_authority(self) -> None:
        """Every supported install reaches the lifecycle system-time repair."""
        loader_source = inspect.getsource(migration_install.apply_foundation_migration)
        self.assertIn(
            "0025_reconciliation_lifecycle_recording_time_authority.sql",
            loader_source,
        )

    def test_transition_recorded_at_is_database_owned_and_future_time_fails_closed(self) -> None:
        """A caller cannot forge system time to make a future decision current."""
        self.assertTrue(_MIGRATION.is_file(), "migration 0025 must be checked in")
        migration = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn("NEW.recorded_at := clock_timestamp();", migration)
        self.assertIn("NEW.effective_at > NEW.recorded_at", migration)
        self.assertIn("reconciliation_lifecycle_future_time", migration)
        self.assertIn(
            "BEFORE INSERT ON accounting_core.reconciliation_run_transition_command",
            migration,
        )


if __name__ == "__main__":
    unittest.main()
