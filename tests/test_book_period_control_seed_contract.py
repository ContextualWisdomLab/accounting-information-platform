"""Static contracts for post-install book-period authority seeding."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0034_book_period_control_seed.sql"
INSTALLER = ROOT / "src/accounting_information_platform/migration_install.py"


class BookPeriodControlSeedContractTests(unittest.TestCase):
    """Keep future master data on the same database-owned period authority boundary."""

    def test_period_and_book_creation_both_seed_controls(self) -> None:
        """Either master-data creation order must materialize the book-period pair."""
        source = MIGRATION.read_text(encoding="utf-8")

        self.assertIn("seed_book_period_control_for_period", source)
        self.assertIn("AFTER INSERT\n    ON accounting_core.fiscal_period", source)
        self.assertIn("seed_book_period_control_for_book", source)
        self.assertIn("AFTER INSERT\n    ON accounting_core.accounting_book", source)
        self.assertGreaterEqual(
            source.count("INSERT INTO accounting_core.accounting_book_period_control"),
            3,
        )
        self.assertGreaterEqual(source.count("ON CONFLICT"), 3)

    def test_trigger_functions_use_hardened_execution_context(self) -> None:
        """Master-data triggers must not inherit caller-controlled object resolution."""
        source = MIGRATION.read_text(encoding="utf-8")

        self.assertEqual(source.count("SECURITY DEFINER"), 2)
        self.assertEqual(source.count("SET search_path = pg_catalog, pg_temp"), 2)
        self.assertIn(
            "REVOKE ALL ON FUNCTION accounting_core.seed_book_period_control_for_period()",
            source,
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION accounting_core.seed_book_period_control_for_book()",
            source,
        )

    def test_canonical_installer_includes_seed_migration(self) -> None:
        """Supported foundation installs cannot stop before future-pair seeding exists."""
        source = INSTALLER.read_text(encoding="utf-8")

        self.assertIn('"0034_book_period_control_seed.sql"', source)


if __name__ == "__main__":
    unittest.main()
