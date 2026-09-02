"""Migration contracts for database-owned reconciliation control recording time."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = (
    _ROOT / "database/migrations/0024_reconciliation_control_recording_time_authority.sql"
)


class ReconciliationRecordingTimeUpgradeContractTests(unittest.TestCase):
    """Keep pre-0024 caller-shaped system time from becoming trusted provenance."""

    def test_legacy_recording_time_rows_fail_closed_before_new_authority(self) -> None:
        """Unverifiable pre-trigger rows must block installation before durable guards."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        marker = "reconciliation_recording_time_legacy_preflight"
        exception_policy = "reconciliation_exception_recording_time_upgrade_visibility"
        evidence_policy = "reconciliation_evidence_recording_time_upgrade_visibility"
        durable_function = (
            "CREATE OR REPLACE FUNCTION "
            "accounting_core.assign_reconciliation_control_recorded_at"
        )

        self.assertIn(marker, migration)
        self.assertIn(
            f"CREATE POLICY {exception_policy}",
            migration,
        )
        self.assertIn(
            f"CREATE POLICY {evidence_policy}",
            migration,
        )
        self.assertIn("FOR SELECT\n    TO current_user\n    USING (true);", migration)
        self.assertIn("FROM accounting_core.reconciliation_exception", migration)
        self.assertIn("FROM accounting_core.reconciliation_evidence", migration)
        self.assertIn(
            f"DROP POLICY {exception_policy}",
            migration,
        )
        self.assertIn(
            f"DROP POLICY {evidence_policy}",
            migration,
        )
        self.assertLess(migration.index(marker), migration.index(durable_function))
        self.assertLess(
            migration.index(f"DROP POLICY {evidence_policy}"),
            migration.index(durable_function),
        )


if __name__ == "__main__":
    unittest.main()
