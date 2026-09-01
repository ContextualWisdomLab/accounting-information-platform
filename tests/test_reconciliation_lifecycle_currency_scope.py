"""Regression coverage for database-owned reconciliation lifecycle currency scope."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from accounting_information_platform.reconciliation_lifecycle import _transition_snapshot_hash


class ReconciliationLifecycleCurrencyScopeTests(unittest.TestCase):
    """Keep lifecycle snapshots bound to run scope rather than projection-only fields."""

    def test_transition_snapshot_accepts_currency_from_locked_run_scope(self) -> None:
        """Currency must come from the locked reconciliation run, not the bridge helper shape."""
        bridge = SimpleNamespace(
            statement_population_reference="sha256:" + "1" * 64,
            book_population_reference="sha256:" + "2" * 64,
            statement_opening_balance=Decimal("100.00"),
            statement_period_movements=Decimal("25.00"),
            statement_closing_balance=Decimal("125.00"),
            book_opening_balance=Decimal("100.00"),
            posted_cash_book_movements=Decimal("25.00"),
            book_closing_balance=Decimal("125.00"),
            reconciled_book_balance=Decimal("125.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
        )

        digest = _transition_snapshot_hash(
            UUID("00000000-0000-0000-0000-000000000043"),
            "sha256:" + "3" * 64,
            "KRW",
            bridge,
            (),
            (),
        )

        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":  # pragma: no cover - direct test execution convenience
    unittest.main()
