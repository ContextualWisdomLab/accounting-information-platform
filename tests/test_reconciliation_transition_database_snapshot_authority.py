"""Regression contract for database-owned reconciliation transition snapshot authority."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_MIGRATION = (
    ROOT
    / "database"
    / "migrations"
    / "0019_reconciliation_run_database_snapshot_authority.sql"
)
INSTALLER = ROOT / "src" / "accounting_information_platform" / "migration_install.py"


class ReconciliationTransitionDatabaseSnapshotAuthorityTests(unittest.TestCase):
    """Require PostgreSQL, not the application caller, to own finalization evidence."""

    def test_checked_in_authority_migration_rederives_snapshot_from_database_facts(self) -> None:
        """The transition trigger must replace caller digests with one database-owned snapshot."""
        sql = AUTHORITY_MIGRATION.read_text(encoding="utf-8")

        self.assertIn("reconciliation_run_database_snapshot_authority", sql)
        self.assertIn("reconciliation_database_bridge_unexplained", sql)
        self.assertIn("statement_population", sql)
        self.assertIn("book_population", sql)
        self.assertIn("reviewed_match_population", sql)
        self.assertIn("exception_population", sql)
        self.assertIn("NEW.reconciliation_snapshot_hash := database_snapshot_hash", sql)
        self.assertIn("NEW.statement_population_reference := database_statement_reference", sql)
        self.assertIn("NEW.book_population_reference := database_book_reference", sql)

    def test_public_installer_applies_authority_after_base_0019(self) -> None:
        """Every supported foundation install must include the database-authority overlay."""
        source = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("0019_reconciliation_run_database_snapshot_authority.sql", source)
        self.assertIn("_persistence.apply_foundation_migration = apply_foundation_migration", source)


if __name__ == "__main__":
    unittest.main()
