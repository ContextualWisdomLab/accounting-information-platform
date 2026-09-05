"""Static contracts for post-install book-period authority seeding."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0034_book_period_control_seed.sql"
BOOK_PERIOD_MIGRATION = ROOT / "database/migrations/0009_accounting_book_period_control.sql"
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

    def test_cross_tenant_backfills_are_owner_safe_without_disabling_rls(self) -> None:
        """Unbound migration owners need owner-only visibility on source and target tables."""
        initial_source = BOOK_PERIOD_MIGRATION.read_text(encoding="utf-8")
        initial_backfill = initial_source.index(
            "INSERT INTO accounting_core.accounting_book_period_control ("
        )
        initial_control_force = initial_source.index(
            "ALTER TABLE accounting_core.accounting_book_period_control FORCE ROW LEVEL SECURITY;"
        )
        initial_book_no_force = initial_source.index(
            "ALTER TABLE accounting_core.accounting_book NO FORCE ROW LEVEL SECURITY;"
        )
        initial_period_no_force = initial_source.index(
            "ALTER TABLE accounting_core.fiscal_period NO FORCE ROW LEVEL SECURITY;"
        )
        initial_book_force = initial_source.rindex(
            "ALTER TABLE accounting_core.accounting_book FORCE ROW LEVEL SECURITY;"
        )
        initial_period_force = initial_source.rindex(
            "ALTER TABLE accounting_core.fiscal_period FORCE ROW LEVEL SECURITY;"
        )
        self.assertLess(initial_book_no_force, initial_backfill)
        self.assertLess(initial_period_no_force, initial_backfill)
        self.assertLess(initial_backfill, initial_book_force)
        self.assertLess(initial_backfill, initial_period_force)
        self.assertLess(initial_backfill, initial_control_force)

        repair_source = MIGRATION.read_text(encoding="utf-8")
        repair_backfill = repair_source.rindex(
            "INSERT INTO accounting_core.accounting_book_period_control ("
        )
        for table_name in (
            "accounting_book",
            "fiscal_period",
            "accounting_book_period_control",
            "period_journal_population_fence",
        ):
            no_force = repair_source.index(
                f"ALTER TABLE accounting_core.{table_name} NO FORCE ROW LEVEL SECURITY;"
            )
            force = repair_source.rindex(
                f"ALTER TABLE accounting_core.{table_name} FORCE ROW LEVEL SECURITY;"
            )
            self.assertLess(no_force, repair_backfill)
            self.assertLess(repair_backfill, force)

        self.assertNotIn("DISABLE ROW LEVEL SECURITY", initial_source)
        self.assertNotIn("DISABLE ROW LEVEL SECURITY", repair_source)

    def test_canonical_installer_includes_seed_migration(self) -> None:
        """Supported foundation installs cannot stop before future-pair seeding exists."""
        source = INSTALLER.read_text(encoding="utf-8")

        self.assertIn('"0034_book_period_control_seed.sql"', source)


if __name__ == "__main__":
    unittest.main()
