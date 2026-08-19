"""Regression contracts for database-owned accounting invariants."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERIOD_GUARD_MIGRATION = ROOT / "database/migrations/0005_closed_period_guard.sql"


class DatabaseInvariantMigrationContracts(unittest.TestCase):
    """Keep authorization and journal-balance controls in PostgreSQL migrations."""

    def test_soft_close_exception_requires_database_role_membership(self) -> None:
        """A caller-controlled GUC cannot be the sole soft-close authorization signal."""
        migration = PERIOD_GUARD_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE ROLE accounting_closing_writer NOLOGIN", migration)
        self.assertIn(
            "pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')",
            migration,
        )
        self.assertIn(
            "journal_write_role_value IN ('period_closing', 'adjusting', 'reversal')",
            migration,
        )

    def test_journal_balance_is_checked_by_deferred_database_triggers(self) -> None:
        """A transaction cannot commit a journal whose persisted debit and credit totals differ."""
        migration = PERIOD_GUARD_MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE OR REPLACE FUNCTION accounting_core.assert_journal_balance()",
            migration,
        )
        self.assertIn("debit_total_value <> credit_total_value", migration)
        self.assertIn("CREATE CONSTRAINT TRIGGER general_journal_balance_guard", migration)
        self.assertIn("CREATE CONSTRAINT TRIGGER journal_entry_balance_guard", migration)
        self.assertGreaterEqual(migration.count("DEFERRABLE INITIALLY DEFERRED"), 2)


if __name__ == "__main__":
    unittest.main()
