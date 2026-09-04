"""Repository contracts for the direct reconciliation lifecycle session-lock boundary."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT_MIGRATION = (
    ROOT
    / "database/migrations/0019_reconciliation_run_database_snapshot_authority.sql"
)
MIGRATION = (
    ROOT
    / "database/migrations/0027_reconciliation_lifecycle_session_lock_authority.sql"
)
INSTALLER = ROOT / "src/accounting_information_platform/migration_install.py"
SESSION_TRIGGER = "accounting_reconciliation_transition_000_session_lock_guard"
AUTHORITY_TRIGGER = "accounting_reconciliation_transition_database_authority_guard"


class ReconciliationLifecycleSessionLockAuthorityContractTests(unittest.TestCase):
    """Keep direct transition authority behind a committed session-lock acquisition lease."""

    def test_session_lock_guard_runs_before_database_snapshot_authority(self) -> None:
        """Unsafe DML must fail before any authority read or predecessor snapshot can be admitted."""
        sql = MIGRATION.read_text(encoding="utf-8")
        parent_sql = PARENT_MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE TABLE accounting_core.reconciliation_lifecycle_session_lease",
            sql,
        )
        self.assertIn("acquisition_transaction_id xid8 NOT NULL", sql)
        self.assertIn(
            "CREATE OR REPLACE FUNCTION accounting_core.acquire_reconciliation_lifecycle_session",
            sql,
        )
        self.assertIn(
            "CREATE OR REPLACE FUNCTION accounting_core.release_reconciliation_lifecycle_session",
            sql,
        )
        self.assertIn("pg_current_xact_id()", sql)
        self.assertIn("transaction_timestamp()", sql)
        self.assertIn("reconciliation_lifecycle_fresh_transaction_required", sql)
        self.assertIn("reconciliation_lifecycle_session_lock_required", sql)
        self.assertIn("pg_advisory_unlock(hashtext(tenant_reference)", sql)
        self.assertIn("FROM pg_catalog.pg_locks AS held_lock", sql)
        self.assertIn("held_lock.pid = pg_backend_pid()", sql)
        self.assertIn("held_lock.objsubid = 2", sql)
        self.assertIn("held_lock.granted", sql)
        self.assertIn("current_setting('transaction_isolation') <> 'repeatable read'", sql)
        self.assertIn(f"CREATE TRIGGER {SESSION_TRIGGER}", sql)
        self.assertIn(f"CREATE TRIGGER {AUTHORITY_TRIGGER}", parent_sql)
        self.assertLess(
            SESSION_TRIGGER,
            AUTHORITY_TRIGGER,
            "PostgreSQL trigger-name ordering must run the lock prerequisite first",
        )

    def test_session_lock_helpers_are_not_public_capabilities(self) -> None:
        """Anonymous database principals cannot acquire or release lifecycle authority locks."""
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            "REVOKE ALL ON FUNCTION accounting_core.acquire_reconciliation_lifecycle_session(text, uuid) FROM PUBLIC;",
            sql,
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION accounting_core.release_reconciliation_lifecycle_session(text, uuid) FROM PUBLIC;",
            sql,
        )

    def test_canonical_installer_requires_session_lock_authority_migration(self) -> None:
        """Supported installs cannot stop before the fresh-transaction authority guard."""
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(
            "0027_reconciliation_lifecycle_session_lock_authority.sql",
            installer,
        )


if __name__ == "__main__":
    unittest.main()
