"""Code-current documentation contracts for durable reconciliation evidence."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReconciliationDataModelDocumentationContractTests(unittest.TestCase):
    """Keep diligence-facing data-model documents aligned with migrations 0013-0015."""

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


if __name__ == "__main__":
    unittest.main()
