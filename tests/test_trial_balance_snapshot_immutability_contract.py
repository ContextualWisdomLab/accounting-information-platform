"""Static contracts for the hard-close trial-balance immutability migrations."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError, apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
INDEX_MIGRATION = (
    ROOT / "database/migrations/0029_trial_balance_snapshot_population_unique_index.sql"
)
IMMUTABILITY_MIGRATION = ROOT / "database/migrations/0030_trial_balance_snapshot_immutability.sql"


class TrialBalanceSnapshotImmutabilityContractTests(unittest.TestCase):
    """Keep the migration chain and database-owned serialization boundary reviewable."""

    def test_canonical_installer_fails_closed_when_snapshot_migration_is_missing(self) -> None:
        """A supported install may not stop before the complete hard-close snapshot boundary."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name in {INDEX_MIGRATION.name, IMMUTABILITY_MIGRATION.name}:
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_population_unique_index_is_built_without_blocking_writes(self) -> None:
        """The large-table uniqueness proof must be built concurrently outside a transaction."""
        index_migration = INDEX_MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE UNIQUE INDEX CONCURRENTLY trial_balance_snapshot_one_population_per_book_period",
            index_migration,
        )
        self.assertIn(
            "ON accounting_reporting.trial_balance_snapshot "
            "(tenant_account_id, accounting_book_id, fiscal_period_id)",
            index_migration,
        )
        self.assertNotIn("BEGIN;", index_migration)
        self.assertNotIn("COMMIT;", index_migration)

        immutability_migration = IMMUTABILITY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            "ADD CONSTRAINT trial_balance_snapshot_one_population_per_book_period",
            immutability_migration,
        )
        self.assertIn(
            "UNIQUE USING INDEX trial_balance_snapshot_one_population_per_book_period",
            immutability_migration,
        )
        self.assertNotIn("CREATE UNIQUE INDEX", immutability_migration)

    def test_population_guards_serialize_on_book_period_authority(self) -> None:
        """Snapshot and line admission must lock the book-period state they authorize."""
        migration = IMMUTABILITY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("trial_balance_snapshot_population_guard", migration)
        self.assertIn("trial_balance_line_population_guard", migration)
        self.assertIn("FOR UPDATE;", migration)
        self.assertIn("FOR UPDATE OF accounting_book_period_control", migration)
        self.assertGreaterEqual(migration.count("period_status_value = 'hard_closed'"), 2)
        self.assertIn("trial_balance_snapshot_immutable", migration)
        self.assertGreaterEqual(migration.count("SECURITY DEFINER"), 2)
        self.assertGreaterEqual(
            migration.count("SET search_path = pg_catalog, pg_temp"),
            2,
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert()",
            migration,
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION accounting_reporting.guard_trial_balance_line_insert()",
            migration,
        )

    def test_snapshot_header_requires_purpose_limited_hard_close_authority(self) -> None:
        """Capability plus close-command context is required; a caller GUC is not authority."""
        migration = IMMUTABILITY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("period_status_value <> 'soft_closed'", migration)
        self.assertIn(
            "journal_write_role_value IS DISTINCT FROM 'period_closing'",
            migration,
        )
        self.assertIn(
            "pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')",
            migration,
        )
        self.assertIn("close_command_lock_held", migration)
        self.assertIn("FROM pg_catalog.pg_locks AS held_lock", migration)
        self.assertIn("held_lock.objsubid = 2", migration)
        self.assertIn("held_lock.pid = pg_backend_pid()", migration)
        self.assertIn(
            "'period:' || accounting_book.book_name || ':' || fiscal_period.period_code",
            migration,
        )
        self.assertIn("trial_balance_snapshot_authority_required", migration)

    def test_snapshot_header_system_time_is_database_owned(self) -> None:
        """Even an admitted closing writer cannot select retained snapshot chronology."""
        migration = IMMUTABILITY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("NEW.snapshot_generated_at := clock_timestamp();", migration)

    def test_book_period_accepts_at_most_one_snapshot_population(self) -> None:
        """Visible and stale snapshots must both be unable to create a second population."""
        migration = IMMUTABILITY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("trial_balance_snapshot_population_conflict", migration)
        self.assertIn(
            "trial_balance_snapshot.accounting_book_id = NEW.accounting_book_id",
            migration,
        )
        self.assertIn(
            "trial_balance_snapshot.fiscal_period_id = NEW.fiscal_period_id",
            migration,
        )

    def test_header_and_line_mutations_are_rejected_at_the_table_boundary(self) -> None:
        """Both retained snapshot levels must reject UPDATE and DELETE before constraints drift."""
        migration = IMMUTABILITY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("trial_balance_snapshot_immutable_guard", migration)
        self.assertIn("trial_balance_line_immutable_guard", migration)
        self.assertGreaterEqual(migration.count("BEFORE UPDATE OR DELETE"), 2)


if __name__ == "__main__":
    unittest.main()
