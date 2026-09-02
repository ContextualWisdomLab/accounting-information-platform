"""Documentation contracts for database-owned reconciliation lifecycle snapshots."""

from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


class ReconciliationLifecycleDatabaseAuthorityDocumentationTests(unittest.TestCase):
    """Keep lifecycle documentation aligned with PostgreSQL snapshot authority."""

    def test_adr_names_database_derived_snapshot_and_rejects_stale_claim(self) -> None:
        """ADR 0060 must not describe the transition digest as application authority."""
        text = (_ROOT / "docs/adr/0060-reconciliation-run-lifecycle-authority.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("reconciliation_run_database_snapshot_hash", text)
        self.assertIn("database-derived reconciliation snapshot", text)
        self.assertNotIn(
            "PostgreSQL stores and binds that digest but does **not** independently rederive",
            text,
        )

    def test_traceability_names_transition_snapshot_trigger_and_real_postgres_red(self) -> None:
        """Standards traceability must point to executable database authority evidence."""
        text = (_ROOT / "docs/doctoring/STANDARD_TRACEABILITY.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("accounting_reconciliation_transition_authority_snapshot_guard", text)
        self.assertIn("test_reconciliation_lifecycle_database_authority_postgres.py", text)


if __name__ == "__main__":
    unittest.main()
