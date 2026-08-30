"""Code-current documentation contracts for durable reconciliation evidence."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReconciliationDataModelDocumentationContractTests(unittest.TestCase):
    """Keep diligence-facing data-model documents aligned with reconciliation migrations."""

    def test_data_model_names_durable_reconciliation_relations(self) -> None:
        """The normalized reconciliation run/match/allocation facts must be explicit."""
        text = (ROOT / "docs/DATA_MODEL.md").read_text(encoding="utf-8")
        for relation in (
            "`reconciliation_run`",
            "`reconciliation_exception`",
            "`reconciliation_candidate`",
            "`reconciliation_match`",
            "`statement_match_allocation`",
            "`journal_match_allocation`",
            "`reconciliation_match_command`",
        ):
            with self.subTest(relation=relation):
                self.assertIn(relation, text)

        future = text.split("## Future extensions", 1)[1]
        self.assertNotIn("deterministic bank-statement matching", future)

    def test_erd_shows_reconciliation_evidence_chain(self) -> None:
        """The ERD must expose the durable run-to-match/allocation control chain."""
        text = (ROOT / "docs/ERD.md").read_text(encoding="utf-8")
        for relation in (
            "reconciliation_run",
            "reconciliation_exception",
            "reconciliation_candidate",
            "reconciliation_match",
            "statement_match_allocation",
            "journal_match_allocation",
        ):
            with self.subTest(relation=relation):
                self.assertIn(relation, text)
        self.assertIn("append-only", text.lower())
        self.assertIn("active", text.lower())
        self.assertIn("approved", text.lower())

    def test_reconciliation_amounts_use_platform_numeric_precision(self) -> None:
        """Forward migration widens reconciliation amounts to the platform precision contract."""
        migration_path = (
            ROOT
            / "database/migrations/0022_reconciliation_amount_precision.sql"
        )
        if not migration_path.is_file():
            self.fail("reconciliation amount precision migration is missing")
        migration = migration_path.read_text(encoding="utf-8")
        self.assertNotIn("numeric(30, 6)", migration)
        for table_name, column_name in (
            ("reconciliation_candidate", "statement_amount"),
            ("reconciliation_candidate", "journal_amount"),
            ("statement_match_allocation", "allocated_amount"),
            ("journal_match_allocation", "allocated_amount"),
        ):
            with self.subTest(table_name=table_name, column_name=column_name):
                self.assertIn(
                    f"ALTER COLUMN {column_name} TYPE numeric(38, 6)",
                    migration,
                )

    def test_precision_migration_rebuilds_column_dependent_candidate_trigger(self) -> None:
        """The type repair must preserve the candidate UPDATE OF trigger registration."""
        migration = (
            ROOT
            / "database/migrations/0022_reconciliation_amount_precision.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "DROP TRIGGER reconciliation_candidate_capacity_guard",
            migration,
        )
        self.assertIn(
            "CREATE TRIGGER reconciliation_candidate_capacity_guard",
            migration,
        )
        self.assertLess(
            migration.index("DROP TRIGGER reconciliation_candidate_capacity_guard"),
            migration.index("ALTER TABLE accounting_core.reconciliation_candidate"),
        )
        self.assertGreater(
            migration.index("CREATE TRIGGER reconciliation_candidate_capacity_guard"),
            migration.index("ALTER COLUMN journal_amount TYPE numeric(38, 6)"),
        )

    def test_precision_migration_rechecks_command_freeze_after_parent_lock(self) -> None:
        """Allocation freeze evidence must use the locked parent marker."""
        migration = (
            ROOT
            / "database/migrations/0022_reconciliation_amount_precision.sql"
        ).read_text(encoding="utf-8")
        marker = (
            "CREATE OR REPLACE FUNCTION "
            "accounting_core.reject_reconciliation_match_command_allocation()"
        )
        self.assertIn(marker, migration)
        function = migration.split(marker, 1)[1].split(
            "CREATE TRIGGER reconciliation_candidate_capacity_guard", 1
        )[0]
        self.assertRegex(
            function,
            r"(?s)FROM accounting_core\.reconciliation_match.*?FOR UPDATE.*?"
            r"command_evidence_recorded_at IS NOT NULL",
        )

    def test_precision_migration_rejects_non_proposed_command_matches(self) -> None:
        """Command provenance can only be recorded for a still-proposed match."""
        migration = (
            ROOT
            / "database/migrations/0022_reconciliation_amount_precision.sql"
        ).read_text(encoding="utf-8")
        function = migration.split(
            "CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_match_command_allocations()",
            1,
        )[1].split(
            "CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_match_command_allocation()",
            1,
        )[0]
        self.assertIn("current_match_status", function)
        self.assertIn("match_status_code", function)
        self.assertIn("reconciliation_match_command_status", function)

    def test_precision_migration_persists_command_freeze_on_parent_match(self) -> None:
        """Allocation guards must read a durable freeze marker from the locked parent."""
        migration = (
            ROOT
            / "database/migrations/0022_reconciliation_amount_precision.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ADD COLUMN command_evidence_recorded_at timestamptz",
            migration,
        )
        self.assertRegex(
            migration,
            r"(?s)UPDATE accounting_core\.reconciliation_match.*?SET command_evidence_recorded_at",
        )
        function = migration.split(
            "CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_match_command_allocation()",
            1,
        )[1].split("CREATE TRIGGER reconciliation_candidate_capacity_guard", 1)[0]
        self.assertIn("command_evidence_recorded_at IS NOT NULL", function)

    def test_precision_migration_makes_command_freeze_marker_immutable(self) -> None:
        """Direct callers cannot clear or rewrite a command freeze marker."""
        migration = (
            ROOT
            / "database/migrations/0022_reconciliation_amount_precision.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "CREATE OR REPLACE FUNCTION "
            "accounting_core.reconciliation_match_command_marker_immutability()",
            migration,
        )
        self.assertIn(
            "BEFORE UPDATE OF command_evidence_recorded_at",
            migration,
        )
        self.assertIn("OLD.command_evidence_recorded_at IS NOT NULL", migration)
        self.assertIn("reconciliation_match_command_marker_immutable", migration)


if __name__ == "__main__":
    unittest.main()
