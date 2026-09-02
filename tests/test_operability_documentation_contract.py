"""Documentation contracts for current accounting operability evidence."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OperabilityDocumentationContractTests(unittest.TestCase):
    """Keep release-operability guidance aligned with verified foundation behavior."""

    def test_reversal_operability_status_matches_current_foundation(self) -> None:
        """Operability must not call an already-proven reversal contract pending."""
        operability = (ROOT / "docs" / "OPERABILITY.md").read_text(encoding="utf-8")

        self.assertNotIn(
            "Treat PR #2 as non-release-ready until that contract passes",
            operability,
        )
        self.assertIn(
            "Current PostgreSQL integration tests prove exact reversal replay",
            operability,
        )

    def test_reconciliation_run_command_is_documented_as_tenant_locked(self) -> None:
        """Run opening must be included in the tenant-scoped lock operations."""
        operability = " ".join(
            (ROOT / "docs" / "OPERABILITY.md").read_text(encoding="utf-8").split()
        )

        self.assertIn(
            "reconciliation-run commands acquire tenant-scoped transaction advisory locks",
            operability,
        )

    def test_reconciliation_outbox_retention_migration_is_operationally_required(self) -> None:
        """Operations must install 0022 and preserve exactly-one authority evidence."""
        operability = (ROOT / "docs" / "OPERABILITY.md").read_text(encoding="utf-8")

        self.assertIn(
            "database/migrations/0022_reconciliation_authority_outbox_retention.sql",
            operability,
        )
        self.assertIn(
            "exactly one matching outbox event",
            operability,
        )
        self.assertIn("published_at", operability)
        self.assertIn("duplicate", operability)


if __name__ == "__main__":
    unittest.main()
