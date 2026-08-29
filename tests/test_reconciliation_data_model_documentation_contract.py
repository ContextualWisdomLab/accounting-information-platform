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


if __name__ == "__main__":
    unittest.main()
