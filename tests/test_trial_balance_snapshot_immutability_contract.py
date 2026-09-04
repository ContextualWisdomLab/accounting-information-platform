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

    def test_population_guard_serializes_on_book_period_authority(self) -> None:
        """Line admission must lock the same book-period row whose hard-close state it checks."""
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("trial_balance_line_population_guard", migration)
        self.assertIn("FOR UPDATE OF accounting_book_period_control", migration)
        self.assertIn("period_status_value = 'hard_closed'", migration)
        self.assertIn("trial_balance_snapshot_immutable", migration)
        self.assertIn("SECURITY DEFINER", migration)
        self.assertIn("pg_temp", migration)

    def test_header_and_line_mutations_are_rejected_at_the_table_boundary(self) -> None:
        """Both retained snapshot levels must reject UPDATE and DELETE before constraints drift."""
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("trial_balance_snapshot_immutable_guard", migration)
        self.assertIn("trial_balance_line_immutable_guard", migration)
        self.assertGreaterEqual(migration.count("BEFORE UPDATE OR DELETE"), 2)


if __name__ == "__main__":
    unittest.main()
