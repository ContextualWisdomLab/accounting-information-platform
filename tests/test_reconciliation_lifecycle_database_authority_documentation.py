"""Documentation contracts for database-owned reconciliation lifecycle snapshots."""

from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


class ReconciliationLifecycleDatabaseAuthorityDocumentationTests(unittest.TestCase):
    """Keep lifecycle documentation aligned with stacked PostgreSQL authority."""

    def test_adr_names_parent_database_authority_and_child_resolution_overlay(self) -> None:
        """ADR 0060 must describe the exact stacked trigger/function ownership."""
        text = (_ROOT / "docs/adr/0060-reconciliation-run-lifecycle-authority.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("reconciliation_run_database_snapshot_authority", text)
        self.assertIn("accounting_reconciliation_transition_database_authority_guard", text)
        self.assertIn("accounting_reconciliation_transition_evidence_snapshot_guard", text)
        self.assertIn("database-derived reconciliation snapshot", text)
        self.assertNotIn("reconciliation_run_database_snapshot_hash", text)
        self.assertNotIn("accounting_reconciliation_transition_authority_snapshot_guard", text)

    def test_traceability_names_parent_and_child_transition_snapshot_evidence(self) -> None:
        """Standards traceability must point to current executable authority evidence."""
        text = (_ROOT / "docs/doctoring/STANDARD_TRACEABILITY.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("accounting_reconciliation_transition_database_authority_guard", text)
        self.assertIn("accounting_reconciliation_transition_evidence_snapshot_guard", text)
        self.assertIn("0020_reconciliation_run_database_snapshot_authority.sql", text)
        self.assertIn("test_reconciliation_lifecycle_database_authority_postgres.py", text)
        self.assertNotIn("accounting_reconciliation_transition_authority_snapshot_guard", text)


if __name__ == "__main__":
    unittest.main()
