"""Fail-closed branch tests for accounting-book period control helpers."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from accounting_information_platform.persistence import (
    AccountingValidationError,
    PostgresPostingLedger,
    apply_foundation_migration,
)


class _ScriptedConnection:
    """Minimal connection double whose fetch results are supplied in order."""

    def __init__(self, rows: list[object | None]) -> None:
        self._rows = iter(rows)
        self.executed: list[tuple[str, object | None]] = []

    def execute(self, statement: str, params: object | None = None) -> "_ScriptedConnection":
        self.executed.append((statement, params))
        return self

    def fetchone(self) -> object | None:
        return next(self._rows)


class BookPeriodHelperBoundaryTests(unittest.TestCase):
    """Cover fail-closed book-period branches without weakening integration tests."""

    def setUp(self) -> None:
        self.ledger = PostgresPostingLedger(
            "postgresql://unused",
            tenant_reference="urn:cwl:tenant:coverage",
        )
        self.tenant_id = uuid4()
        self.book_id = uuid4()
        self.period_id = uuid4()
        self.accounting_date = date(2026, 8, 31)

    def _period_row(self, status: str = "open") -> tuple[object, ...]:
        return (
            self.period_id,
            "2026-08",
            status,
            date(2026, 8, 1),
            date(2026, 8, 31),
        )

    def test_open_book_period_rejects_missing_initial_period(self) -> None:
        connection = _ScriptedConnection([None])
        with self.assertRaisesRegex(AccountingValidationError, "No fiscal period covers"):
            self.ledger._require_open_book_period_bounds(
                connection, self.tenant_id, self.book_id, self.accounting_date
            )

    def test_open_book_period_rejects_period_removed_after_lock(self) -> None:
        connection = _ScriptedConnection([self._period_row(), None])
        with self.assertRaisesRegex(AccountingValidationError, "No fiscal period covers"):
            self.ledger._require_open_book_period_bounds(
                connection, self.tenant_id, self.book_id, self.accounting_date
            )

    def test_open_book_period_reports_soft_and_hard_close_distinctly(self) -> None:
        for status, expected in (
            ("soft_closed", "soft_closed"),
            ("hard_closed", r"hard_closed \(period_closed\)"),
        ):
            with self.subTest(status=status):
                connection = _ScriptedConnection(
                    [self._period_row(status), self._period_row(status)]
                )
                with self.assertRaisesRegex(AccountingValidationError, expected):
                    self.ledger._require_open_book_period_bounds(
                        connection,
                        self.tenant_id,
                        self.book_id,
                        self.accounting_date,
                    )

    def test_open_book_period_returns_locked_bounds(self) -> None:
        connection = _ScriptedConnection([self._period_row(), self._period_row()])
        self.assertEqual(
            self.ledger._require_open_book_period_bounds(
                connection, self.tenant_id, self.book_id, self.accounting_date
            ),
            (self.period_id, date(2026, 8, 1), date(2026, 8, 31)),
        )

    def test_lock_book_period_rejects_missing_calendar_period(self) -> None:
        connection = _ScriptedConnection([None])
        with self.assertRaisesRegex(AccountingValidationError, "not recorded for this tenant"):
            self.ledger._lock_book_period(
                connection, self.tenant_id, self.book_id, "2026-08"
            )

    def test_lock_book_period_rejects_missing_control_row(self) -> None:
        connection = _ScriptedConnection(
            [(self.period_id, "open", None), None]
        )
        with self.assertRaisesRegex(AccountingValidationError, "no control row"):
            self.ledger._lock_book_period(
                connection, self.tenant_id, self.book_id, "2026-08"
            )

    def test_load_book_period_state_preserves_missing_state(self) -> None:
        connection = _ScriptedConnection([None])
        self.assertIsNone(
            self.ledger._load_book_period_state(
                connection, self.tenant_id, self.book_id, "2026-08"
            )
        )

    def test_set_book_period_closed_covers_open_and_closed_aggregate_states(self) -> None:
        closed_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        for aggregate_row in ((None, None), ("soft_closed", closed_at)):
            with self.subTest(aggregate_row=aggregate_row):
                connection = _ScriptedConnection([(closed_at,), aggregate_row])
                self.assertEqual(
                    self.ledger._set_book_period_closed(
                        connection,
                        self.tenant_id,
                        self.book_id,
                        self.period_id,
                        "soft_closed",
                    ),
                    closed_at,
                )

    def test_adjusting_period_bounds_rejects_missing_calendar_period(self) -> None:
        connection = _ScriptedConnection([None])
        with self.assertRaisesRegex(AccountingValidationError, "No fiscal period covers"):
            self.ledger._require_adjusting_period_bounds(
                connection, self.tenant_id, self.accounting_date
            )

    def test_migration_loader_fails_closed_when_book_period_migration_is_missing(self) -> None:
        migration_names = (
            "0001_accounting_foundation.sql",
            "0002_chart_account_class.sql",
            "0003_home_tax_submission.sql",
            "0004_close_idempotency_key.sql",
            "0005_closed_period_guard.sql",
            "0006_concurrency_hot_partition.sql",
            "0007_runtime_tenant_binding.sql",
            "0008_fiscal_period_open_command.sql",
        )
        with tempfile.TemporaryDirectory() as directory:
            migration_root = Path(directory)
            for migration_name in migration_names:
                (migration_root / migration_name).write_text("-- test fixture\n", encoding="utf-8")
            with self.assertRaisesRegex(AccountingValidationError, "0009_accounting_book_period_control.sql"):
                apply_foundation_migration(
                    "postgresql://unused",
                    migration_root / "0001_accounting_foundation.sql",
                )


if __name__ == "__main__":
    unittest.main()
