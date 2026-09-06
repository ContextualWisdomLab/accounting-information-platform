"""Regression contracts for deterministic reconciliation source-lock ordering."""

from __future__ import annotations

from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    REPOSITORY_ROOT / "database/migrations/0015_reconciliation_multi_match_conservation.sql"
)


class ReconciliationSourceLockOrderRedTests(unittest.TestCase):
    """Require every approval source-lock loop to acquire shared keys deterministically."""

    def test_statement_source_lock_loop_orders_grouped_source_keys(self) -> None:
        """Statement source advisory locks must use one stable lexical source order."""
        normalized = " ".join(MIGRATION_PATH.read_text(encoding="utf-8").lower().split())
        self.assertRegex(
            normalized,
            r"for source_row in select allocation\.statement_entry_reference, .*? "
            r"group by allocation\.statement_entry_reference "
            r"order by allocation\.statement_entry_reference loop",
            "Concurrent approvals sharing statement sources must acquire advisory locks "
            "in deterministic statement source order.",
        )

    def test_journal_source_lock_loop_orders_grouped_source_keys(self) -> None:
        """Journal source advisory locks must use one stable lexical source order."""
        normalized = " ".join(MIGRATION_PATH.read_text(encoding="utf-8").lower().split())
        self.assertRegex(
            normalized,
            r"for source_row in select allocation\.journal_reference, .*? "
            r"group by allocation\.journal_reference "
            r"order by allocation\.journal_reference loop",
            "Concurrent approvals sharing journal sources must acquire advisory locks "
            "in deterministic journal source order.",
        )


if __name__ == "__main__":
    unittest.main()
