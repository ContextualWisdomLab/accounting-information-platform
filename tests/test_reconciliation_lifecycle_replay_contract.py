"""Contracts for exact reconciliation lifecycle replay provenance."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from accounting_information_platform.reconciliation_lifecycle import _load_transition_document


_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "database"
    / "migrations"
    / "0019_reconciliation_run_command_evidence.sql"
)


class _Result:
    """Return one immutable transition row to the receipt loader."""

    def fetchone(self) -> tuple[object, ...]:
        """Return persisted transition and population evidence."""
        moment = datetime(2026, 9, 1, 14, 45, tzinfo=timezone.utc)
        return (
            UUID("55555555-5555-4555-8555-555555555555"),
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            "urn:cwl:principal:controller",
            "month_end_control",
            moment,
            moment,
            "reconciled",
            "sha256:" + "3" * 64,
            "sha256:" + "4" * 64,
        )


class _Connection:
    """Minimal receipt-query connection double."""

    def execute(self, _query: str, _params: object) -> _Result:
        """Return the persisted transition row."""
        return _Result()


class ReconciliationLifecycleReplayContractTests(unittest.TestCase):
    """Make first execution and exact replay return the same provenance shape."""

    def test_transition_table_persists_exact_population_references(self) -> None:
        """Immutable command evidence must retain statement and book population identities."""
        migration = _MIGRATION.read_text(encoding="utf-8")

        self.assertIn("statement_population_reference text NOT NULL", migration)
        self.assertIn("book_population_reference text NOT NULL", migration)

    def test_persisted_receipt_returns_population_references_on_replay(self) -> None:
        """Exact replay must not drop source-population provenance returned on first success."""
        document = _load_transition_document(
            _Connection(),
            UUID("66666666-6666-4666-8666-666666666666"),
            "urn:cwl:tenant_test",
            UUID("77777777-7777-4777-8777-777777777777"),
            "reconcile-command-1",
            replayed=True,
        )

        self.assertEqual(document["statement_population_reference"], "sha256:" + "3" * 64)
        self.assertEqual(document["book_population_reference"], "sha256:" + "4" * 64)
        self.assertTrue(document["replayed"])


if __name__ == "__main__":
    unittest.main()
