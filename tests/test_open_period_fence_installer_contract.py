"""Installer and migration-order contracts for the open-period freshness fence."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError, apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
OPEN_PERIOD_FENCE_MIGRATION = (
    ROOT / "database/migrations/0033_open_period_journal_population_fence.sql"
)


class OpenPeriodFenceInstallerContractTests(unittest.TestCase):
    """Prevent supported installs from omitting or tenant-scoping the 0033 backfill."""

    def test_installer_fails_closed_when_0033_is_missing(self) -> None:
        """The canonical migration chain may not stop before the open-period freshness fence."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == OPEN_PERIOD_FENCE_MIGRATION.name:
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_cross_tenant_fence_backfill_precedes_force_rls(self) -> None:
        """Migration-owned seed data is complete before runtime tenant isolation is forced."""
        migration = OPEN_PERIOD_FENCE_MIGRATION.read_text(encoding="utf-8")
        backfill = "INSERT INTO accounting_core.period_journal_population_fence"
        force_rls = (
            "ALTER TABLE accounting_core.period_journal_population_fence "
            "FORCE ROW LEVEL SECURITY"
        )
        self.assertIn(backfill, migration)
        self.assertIn(force_rls, migration)
        self.assertLess(migration.index(backfill), migration.index(force_rls))
        self.assertIn("CROSS JOIN generate_series(0, 63)", migration)
        self.assertIn("period_journal_population_fence_seed", migration)


if __name__ == "__main__":
    unittest.main()
