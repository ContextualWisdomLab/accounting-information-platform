"""RED repository contracts for the reconciliation allocation trigger surface."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0015_reconciliation_multi_match_conservation.sql"


class ReconciliationAllocationGuardContractRedTests(unittest.TestCase):
    """Keep migration 0015 honest about where monetary conservation is enforced."""

    @staticmethod
    def _function_body(name: str) -> str:
        migration = MIGRATION.read_text(encoding="utf-8")
        match = re.search(
            rf"CREATE OR REPLACE FUNCTION accounting_core\.{re.escape(name)}\(\).*?AS \$\$(.*?)\$\$;",
            migration,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            raise AssertionError(f"Missing migration function: {name}")
        return re.sub(r"\s+", " ", match.group(1).lower())

    def test_insert_guard_contains_no_unreachable_approved_status_branch(self) -> None:
        """Proposed-only allocation inserts must not advertise dead approved-state checks."""
        body = self._function_body("reconciliation_allocation_conservation_guard")
        self.assertIn("if current_match_status <> 'proposed' then", body)
        self.assertNotRegex(
            body,
            r"\bif\s+\(?\s*current_match_status\s*(?:=\s*'approved'|in\s*\(\s*'approved'\s*\))",
            "The insert guard rejects every non-proposed match first, so approved-state branches are dead code. Keep source-capacity enforcement at the terminal approval guard instead of retaining unreachable controls.",
        )

    def test_terminal_approval_guard_remains_the_active_capacity_boundary(self) -> None:
        """Removing dead insert branches must not weaken active-run source conservation."""
        body = self._function_body("reconciliation_match_approval_conservation_guard")
        for contract in (
            "pg_advisory_xact_lock",
            "approved_match.match_status_code = 'approved'",
            "reconciliation_allocation_overconsumed",
            "statement_allocation_total <> journal_allocation_total",
        ):
            self.assertIn(contract, body)


if __name__ == "__main__":
    unittest.main()
