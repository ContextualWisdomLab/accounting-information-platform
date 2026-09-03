"""Repository contracts for the direct reconciliation lifecycle session-lock boundary."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "database/migrations/0027_reconciliation_lifecycle_session_lock_authority.sql"
)
INSTALLER = ROOT / "src/accounting_information_platform/migration_install.py"


class ReconciliationLifecycleSessionLockAuthorityContractTests(unittest.TestCase):
    """Keep raw transition authority behind the application's pre-statement lock protocol."""

    def test_session_lock_guard_runs_before_database_snapshot_authority(self) -> None:
        """The raw transition trigger must reject unsafe DML before any authority read."""
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("reconciliation_lifecycle_session_lock_required", sql)
        self.assertIn("FROM pg_catalog.pg_locks AS held_lock", sql)
        self.assertIn("held_lock.pid = pg_backend_pid()", sql)
        self.assertIn("held_lock.objsubid = 2", sql)
        self.assertIn("held_lock.granted", sql)
        self.assertIn(
            "CREATE TRIGGER accounting_reconciliation_transition_000_session_lock_guard",
            sql,
        )
        self.assertLess(
            "accounting_reconciliation_transition_000_session_lock_guard",
            "accounting_reconciliation_transition_database_authority_guard",
        )

    def test_canonical_installer_requires_session_lock_authority_migration(self) -> None:
        """Supported installs cannot stop before the direct-DML freshness guard."""
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(
            "0027_reconciliation_lifecycle_session_lock_authority.sql",
            installer,
        )


if __name__ == "__main__":
    unittest.main()
