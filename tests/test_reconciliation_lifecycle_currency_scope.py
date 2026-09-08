"""Regression coverage for database-owned reconciliation lifecycle currency scope."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from accounting_information_platform.reconciliation_lifecycle import _transition_snapshot_hash


class ReconciliationLifecycleCurrencyScopeTests(unittest.TestCase):
    """Keep lifecycle snapshots bound to run scope rather than projection-only fields."""

    def test_transition_snapshot_binds_currency_from_locked_run_scope(self) -> None:
        """Changing the authoritative run currency must change the transition digest."""
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
        run_id = UUID("00000000-0000-0000-0000-000000000043")
        command_hash = "sha256:" + "3" * 64

        krw_digest = _transition_snapshot_hash(
            run_id,
            command_hash,
            bridge,
            (),
            (),
            currency_code="KRW",
        )
        usd_digest = _transition_snapshot_hash(
            run_id,
            command_hash,
            bridge,
            (),
            (),
            currency_code="USD",
        )

        self.assertRegex(krw_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(usd_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(krw_digest, usd_digest)


if __name__ == "__main__":  # pragma: no cover - direct test execution convenience
    unittest.main()
