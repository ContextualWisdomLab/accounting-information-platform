"""Regression contracts for database-owned accounting invariants."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_TAX_MIGRATION = ROOT / "database/migrations/0003_home_tax_submission.sql"
CLOSE_IDEMPOTENCY_MIGRATION = ROOT / "database/migrations/0004_close_idempotency_key.sql"
PERIOD_GUARD_MIGRATION = ROOT / "database/migrations/0005_closed_period_guard.sql"
CONCURRENCY_MIGRATION = ROOT / "database/migrations/0006_concurrency_hot_partition.sql"


class DatabaseInvariantMigrationContracts(unittest.TestCase):
    """Keep authorization and journal-balance controls in PostgreSQL migrations."""

    def test_home_tax_history_index_matches_scope_and_stable_order(self) -> None:
        """HomeTax history can use one tenant-scoped index for its filter and stable ordering."""
        migration = HOME_TAX_MIGRATION.read_text(encoding="utf-8")
        self.assertRegex(
            migration,
            re.compile(
                r"CREATE\s+INDEX\s+home_tax_submission_scope_order_index\s+"
                r"ON\s+accounting_integration\.home_tax_submission\s*\(\s*"
                r"tenant_account_id\s*,\s*legal_entity_id\s*,\s*"
                r"accounting_book_id\s*,\s*fiscal_period_id\s*,\s*"
                r"created_at\s*,\s*home_tax_submission_id\s*\)",
                re.IGNORECASE | re.MULTILINE,
            ),
        )

    def test_close_idempotency_key_is_unique_inside_tenant_scope(self) -> None:
        """One tenant command key cannot identify multiple close outcomes."""
        migration = CLOSE_IDEMPOTENCY_MIGRATION.read_text(encoding="utf-8")
        self.assertRegex(
            migration,
            re.compile(
                r"UNIQUE\s*\(\s*tenant_account_id\s*,\s*close_idempotency_key\s*\)",
                re.IGNORECASE | re.MULTILINE,
            ),
        )

    def test_close_backfill_key_includes_full_snapshot_scope(self) -> None:
        """Historical close keys cannot collide across entity/book snapshots in one period."""
        migration = CLOSE_IDEMPOTENCY_MIGRATION.read_text(encoding="utf-8")
        assignment = migration.split("SET close_idempotency_key =", 1)[1].split("\nFROM ", 1)[0]
        for scoped_identifier in (
            "snapshot_record.legal_entity_id",
            "snapshot_record.accounting_book_id",
            "snapshot_record.fiscal_period_id",
        ):
            with self.subTest(scoped_identifier=scoped_identifier):
                self.assertIn(scoped_identifier, assignment)
        self.assertIn("tenant_record.tenant_account_code", assignment)
        self.assertIn("period_record.period_code", assignment)

    def test_soft_close_exception_requires_database_role_membership(self) -> None:
        """A caller-controlled GUC cannot be the sole soft-close authorization signal."""
        migration = PERIOD_GUARD_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE ROLE accounting_closing_writer NOLOGIN", migration)
        self.assertIn("ALTER ROLE accounting_closing_writer NOLOGIN", migration)
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
        for trigger_name, table_name in (
            ("general_journal_balance_guard", "accounting_core.general_journal"),
            ("journal_entry_balance_guard", "accounting_core.journal_entry_line"),
        ):
            with self.subTest(trigger_name=trigger_name):
                self.assertRegex(
                    migration,
                    re.compile(
                        rf"DROP\s+TRIGGER\s+IF\s+EXISTS\s+{trigger_name}\s+ON\s+{re.escape(table_name)}",
                        re.IGNORECASE | re.MULTILINE,
                    ),
                )
        self.assertGreaterEqual(migration.count("DEFERRABLE INITIALLY DEFERRED"), 2)

    def test_hot_write_tables_have_tenant_leading_indexes(self) -> None:
        """High-write tenant scans stay bounded before physical partitioning is introduced."""
        migration = CONCURRENCY_MIGRATION.read_text(encoding="utf-8")
        for index_name, table_name in (
            (
                "journal_proposal_tenant_received_index",
                "accounting_integration.journal_proposal_record",
            ),
            (
                "general_journal_tenant_period_date_index",
                "accounting_core.general_journal",
            ),
            (
                "outbox_event_pending_created_index",
                "accounting_integration.outbox_event",
            ),
        ):
            with self.subTest(index_name=index_name):
                self.assertRegex(
                    migration,
                    re.compile(
                        rf"CREATE INDEX {index_name}\s+ON {re.escape(table_name)}",
                        re.IGNORECASE | re.MULTILINE,
                    ),
                )
        self.assertIn("WHERE published_at IS NULL", migration)

    def test_concurrency_migration_documents_partition_ready_contract(self) -> None:
        """The schema records why tenant-leading indexes precede a partition migration."""
        migration = CONCURRENCY_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("tenant-leading", migration)
        self.assertIn("partition", migration.lower())


if __name__ == "__main__":
    unittest.main()
