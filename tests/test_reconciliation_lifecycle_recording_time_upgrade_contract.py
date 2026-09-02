"""Upgrade contracts for reconciliation lifecycle recording-time authority."""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from accounting_information_platform import reconciliation_close_package
from accounting_information_platform import reconciliation_lifecycle

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "database/migrations/0025_reconciliation_lifecycle_recording_time_authority.sql"


class ReconciliationLifecycleRecordingTimeUpgradeContractTests(unittest.TestCase):
    """Prevent pre-0025 caller-shaped system time from becoming close authority."""

    def test_upgrade_marks_existing_transition_time_as_legacy_unverified(self) -> None:
        """Existing transition timestamps remain audit evidence but are not trusted clocks."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        self.assertIn("recording_time_authority_code", migration)
        self.assertIn("legacy_unverified", migration)
        self.assertIn("database_clock", migration)
        self.assertIn("NEW.recording_time_authority_code := 'database_clock';", migration)

    def test_close_package_requires_database_clock_transition_authority(self) -> None:
        """A reconciled status alone cannot promote an unverified legacy transition."""
        source = inspect.getsource(
            reconciliation_close_package._database_owned_run_source_evidence
        )
        self.assertIn("reconciliation_run_transition_command", source)
        self.assertIn("recording_time_authority_code", source)
        self.assertIn("database_clock", source)

    def test_lifecycle_replay_rejects_legacy_unverified_transition_time(self) -> None:
        """Exact replay cannot present a pre-0025 transition as current trusted authority."""
        source = inspect.getsource(reconciliation_lifecycle.reconcile_reconciliation_run)
        self.assertIn("recording_time_authority_code", source)
        self.assertIn("database_clock", source)
        self.assertIn("legacy lifecycle transition", source)


if __name__ == "__main__":
    unittest.main()
