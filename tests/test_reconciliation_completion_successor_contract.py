"""Regression contract for the pre-release reconciliation completion successor."""

from __future__ import annotations

from pathlib import Path
import unittest

from accounting_information_platform import reconciliation_lifecycle


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "database" / "migrations"
INSTALLER = ROOT / "src" / "accounting_information_platform" / "migration_install.py"
LIFECYCLE = ROOT / "src" / "accounting_information_platform" / "reconciliation_lifecycle.py"


class ReconciliationCompletionSuccessorContractTests(unittest.TestCase):
    """Keep one reconciliation lifecycle writer while retaining migration identity."""

    def test_0020_is_an_explicit_non_competing_successor_marker(self) -> None:
        """The retained 0020 identity must not reinstall the superseded command model."""
        sql = (MIGRATIONS / "0020_reconciliation_run_completion_evidence.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("reconciliation_lifecycle_successor_required", sql)
        self.assertIn("reconciliation_run_transition_command", sql)
        self.assertNotIn(
            "CREATE TABLE accounting_core.reconciliation_run_completion_command", sql
        )
        self.assertNotIn("guard_reconciliation_run_status_transition", sql)
        self.assertNotIn("record_reconciliation_completion_evidence", sql)

    def test_successor_retains_stronger_command_status_freeze_and_source_authority(self) -> None:
        """0019/0021 and the owner service must carry the valid completion controls."""
        lifecycle_sql = (MIGRATIONS / "0019_reconciliation_run_command_evidence.sql").read_text(
            encoding="utf-8"
        )
        authority_sql = (
            MIGRATIONS / "0021_reconciliation_run_database_snapshot_authority.sql"
        ).read_text(encoding="utf-8")
        lifecycle_source = LIFECYCLE.read_text(encoding="utf-8")

        for marker in (
            "CREATE TABLE accounting_core.reconciliation_run_transition_command",
            "reconciliation_run_transition_immutable_guard",
            "reconciliation_run_transition_status_pair_guard",
            "accounting_reconciliation_run_transition_guard",
            "reconciliation_lifecycle_frozen",
            "reconciliation_run_transition_command_isolation",
        ):
            self.assertIn(marker, lifecycle_sql)
        self.assertIn("reconciliation_run_database_snapshot_authority", authority_sql)
        self.assertIn("reconciliation_database_bridge_unexplained", authority_sql)
        self.assertIn("'reconciliation_run_reconciled'", lifecycle_source)
        self.assertIn("INSERT INTO accounting_integration.outbox_event", lifecycle_source)

    def test_zero_approved_matches_are_not_a_false_completion_prerequisite(self) -> None:
        """A fully reviewed run may reconcile through exact outstanding-item evidence alone."""
        reconciliation_lifecycle._validate_review_control_state((), ())

    def test_installer_executes_successor_identity_before_snapshot_authority(self) -> None:
        """Supported installs must preflight and execute 0020 then 0021 in order."""
        source = INSTALLER.read_text(encoding="utf-8")
        completion = '"0020_reconciliation_run_completion_evidence.sql"'
        authority = '"0021_reconciliation_run_database_snapshot_authority.sql"'

        self.assertIn("_FORWARD_MIGRATIONS", source)
        self.assertLess(source.index(completion), source.index(authority))
        self.assertIn("for forward_migration_path in forward_migration_paths", source)


if __name__ == "__main__":
    unittest.main()
