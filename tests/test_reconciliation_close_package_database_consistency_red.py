"""RED tests for one transactionally consistent, book-scoped close snapshot."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from accounting_information_platform import reconciliation_close_package as close_package


class _ConsistentReadReached(RuntimeError):
    """Signal that the close-package builder entered the required snapshot session."""


class _SnapshotLedger:
    """Fail if the authority-bearing builder opens an ordinary READ COMMITTED session."""

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextmanager
    def _session(self):
        raise AssertionError(
            "authority-bearing reconciliation close reads must not use the ordinary session"
        )
        yield  # pragma: no cover - makes this a contextmanager generator

    @contextmanager
    def _consistent_read_session(self):
        raise _ConsistentReadReached
        yield  # pragma: no cover - makes this a contextmanager generator


class _Rows:
    """Minimal complete-result wrapper for database authority fixtures."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _BookScopedAuthorityConnection:
    """Require the cash-ledger population query to stay inside the run's accounting book."""

    def execute(self, query: str, parameters: tuple[object, ...]) -> _Rows:
        if "bank_statement_record AS statement" in query and "opening_balance_hash" in query:
            return _Rows(
                [
                    (
                        "statement-record-1",
                        "balance-opening-hash",
                        "balance-closing-hash",
                        datetime(2026, 8, 1, tzinfo=timezone.utc),
                        datetime(2026, 8, 31, 23, 59, 59, tzinfo=timezone.utc),
                        date(2026, 8, 31),
                        datetime(2026, 8, 31, 23, 59, 59, tzinfo=timezone.utc),
                        "cash-chart-id",
                        "KRW",
                    )
                ]
            )
        if "bank_statement_balance AS balance" in query:
            return _Rows(
                [
                    ("balance-opening-hash", Decimal("1000.000000"), "KRW", "CRDT"),
                    ("balance-closing-hash", Decimal("1100.000000"), "KRW", "CRDT"),
                ]
            )
        if "bank_statement_entry AS entry" in query:
            return _Rows(
                [
                    (
                        "stmt-001",
                        1,
                        Decimal("100.000000"),
                        "KRW",
                        "CRDT",
                        False,
                        "sha256:" + "1" * 64,
                    )
                ]
            )
        if "journal_entry_line AS line" in query:
            self.assert_book_scope(query)
            return _Rows(
                [
                    (
                        "journal-opening",
                        date(2026, 7, 31),
                        datetime(2026, 7, 31, 12, tzinfo=timezone.utc),
                        1,
                        Decimal("1000.000000"),
                        Decimal("0"),
                        "KRW",
                    ),
                    (
                        "journal-001",
                        date(2026, 8, 15),
                        datetime(2026, 8, 15, 12, tzinfo=timezone.utc),
                        1,
                        Decimal("100.000000"),
                        Decimal("0"),
                        "KRW",
                    ),
                ]
            )
        if "statement_match_allocation AS allocation" in query:
            return _Rows([("stmt-001", Decimal("100.000000"))])
        if "journal_match_allocation AS allocation" in query:
            return _Rows([("journal-001", Decimal("100.000000"))])
        raise AssertionError(f"unexpected authority query: {query}")

    @staticmethod
    def assert_book_scope(query: str) -> None:
        if "JOIN accounting_core.chart_account AS cash_account" not in query:
            raise AssertionError(
                "cash journal population must join the chart account that owns the assignment"
            )
        if "journal.accounting_book_id = cash_account.accounting_book_id" not in query:
            raise AssertionError(
                "cash journal population must reject lines carried by a different accounting book"
            )


class ReconciliationClosePackageDatabaseConsistencyTests(unittest.TestCase):
    """Require close evidence to be a repeatable, accounting-book-scoped database snapshot."""

    def test_authority_builder_uses_repeatable_read_snapshot_session(self) -> None:
        package_input = close_package.ReconciliationClosePackageInput(
            projection=SimpleNamespace(tenant_account_reference="tenant-1"),
            approval_evidence=(),
            knowledge_cutoff="",
            evidence_references=(),
        )
        with patch.object(close_package, "PostgresPostingLedger", _SnapshotLedger):
            with self.assertRaises(_ConsistentReadReached):
                close_package.build_reconciliation_close_package(
                    package_input,
                    database_url="postgresql://authority.test/accounting",
                    tenant_reference="tenant-1",
                )

    def test_database_cash_population_is_scoped_to_assignment_accounting_book(self) -> None:
        result = close_package._database_owned_close_projection_evidence(
            _BookScopedAuthorityConnection(),
            "tenant-id",
            reconciliation_run_reference="run-001",
        )
        self.assertEqual(result.book_closing_balance, Decimal("1100.000000"))
        self.assertEqual(result.unexplained_difference, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
