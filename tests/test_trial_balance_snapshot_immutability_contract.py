"""Static contracts for the hard-close trial-balance immutability migration."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError, apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0029_trial_balance_snapshot_immutability.sql"


class TrialBalanceSnapshotImmutabilityContractTests(unittest.TestCase):
    """Keep the migration installed and its database-owned serialization boundary reviewable."""

    def test_canonical_installer_fails_closed_when_immutability_migration_is_missing(self) -> None:
        """A supported install may not stop before immutable hard-close snapshot evidence."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == MIGRATION.name:
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_population_guards_serialize_on_book_period_authority(self) -> None:
        """Snapshot and line admission must lock the book-period state they authorize."""
        migration = MIGRATION.read_text(encoding="utf-8")
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

    def test_book_period_accepts_at_most_one_snapshot_population(self) -> None:
        """Visible and stale snapshots must both be unable to create a second population."""
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("trial_balance_snapshot_population_conflict", migration)
        self.assertIn(
            "trial_balance_snapshot.accounting_book_id = NEW.accounting_book_id",
            migration,
        )
        self.assertIn(
            "trial_balance_snapshot.fiscal_period_id = NEW.fiscal_period_id",
            migration,
        )
        self.assertIn(
            "ADD CONSTRAINT trial_balance_snapshot_one_population_per_book_period",
            migration,
        )
        self.assertIn(
            "UNIQUE (tenant_account_id, accounting_book_id, fiscal_period_id)",
            migration,
        )

    def test_header_and_line_mutations_are_rejected_at_the_table_boundary(self) -> None:
        """Both retained snapshot levels must reject UPDATE and DELETE before constraints drift."""
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("trial_balance_snapshot_immutable_guard", migration)
        self.assertIn("trial_balance_line_immutable_guard", migration)
        self.assertGreaterEqual(migration.count("BEFORE UPDATE OR DELETE"), 2)


if __name__ == "__main__":
    unittest.main()
