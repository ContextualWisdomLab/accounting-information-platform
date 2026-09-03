"""Regression contract for stacked reconciliation snapshot authority."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PARENT_AUTHORITY_MIGRATION = (
    ROOT
    / "database"
    / "migrations"
    / "0019_reconciliation_run_database_snapshot_authority.sql"
)
CHILD_AUTHORITY_MIGRATION = (
    ROOT
    / "database"
    / "migrations"
    / "0021_reconciliation_exception_resolution_outbox_pair.sql"
)


class ReconciliationResolutionSnapshotOverlayContractTests(unittest.TestCase):
    """Keep child resolution evidence inside the final database-owned snapshot."""

    def test_child_resolution_overlay_runs_between_parent_authority_and_command_hash(self) -> None:
        """Lexical trigger order must preserve parent authority and then bind child evidence."""
        parent_trigger = "accounting_reconciliation_transition_database_authority_guard"
        child_trigger = "accounting_reconciliation_transition_evidence_snapshot_guard"
        hash_trigger = "accounting_reconciliation_transition_hash_guard"
        parent_sql = PARENT_AUTHORITY_MIGRATION.read_text(encoding="utf-8")
        child_sql = CHILD_AUTHORITY_MIGRATION.read_text(encoding="utf-8")

        self.assertIn(parent_trigger, parent_sql)
        self.assertIn(child_trigger, child_sql)
        self.assertLess(parent_trigger, child_trigger)
        self.assertLess(child_trigger, hash_trigger)
        self.assertNotIn(
            "accounting_reconciliation_transition_authority_snapshot_guard",
            child_sql,
        )

    def test_child_resolution_overlay_hashes_parent_owned_values_and_resolution_commands(self) -> None:
        """The final digest must bind parent-owned populations plus immutable resolution evidence."""
        sql = CHILD_AUTHORITY_MIGRATION.read_text(encoding="utf-8")

        required_tokens = (
            "NEW.reconciliation_snapshot_hash",
            "NEW.statement_population_reference",
            "NEW.book_population_reference",
            "reconciliation_exception_resolution_command_hash",
            "resolution_evidence_hash",
            "target_resolution_status_code",
            "reconciliation_run_resolution_snapshot:v1|",
        )
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, sql)

    def test_snapshot_hash_serializes_temporal_values_canonically(self) -> None:
        """Database-owned snapshot hashes must not depend on session TimeZone or DateStyle."""
        parent_sql = PARENT_AUTHORITY_MIGRATION.read_text(encoding="utf-8")
        child_sql = CHILD_AUTHORITY_MIGRATION.read_text(encoding="utf-8")

        for expression in (
            "journal.posted_at",
            "exception.effective_at",
            "knowledge_cutoff_at",
        ):
            with self.subTest(parent_timestamp=expression):
                self.assertRegex(
                    parent_sql,
                    rf"to_char\(\s*{re.escape(expression)}\s+AT TIME ZONE 'UTC'",
                )
        self.assertRegex(
            parent_sql,
            r"to_char\(\s*journal\.accounting_date\s*,\s*'YYYY-MM-DD'\s*\)",
        )
        for expression in ("resolution.effective_at", "resolution.recorded_at"):
            with self.subTest(child_timestamp=expression):
                self.assertRegex(
                    child_sql,
                    rf"to_char\(\s*{re.escape(expression)}\s+AT TIME ZONE 'UTC'",
                )


if __name__ == "__main__":
    unittest.main()
