"""RED tests for database-owned reconciliation close populations and exact balances."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from accounting_information_platform import reconciliation_close_package as close_package


class _Rows:
    """Minimal result object for deterministic database-authority fixtures."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return the complete configured population."""
        return self._rows


class _AuthorityConnection:
    """Expose immutable statement, ledger, and approved-allocation populations."""

    def __init__(
        self,
        *,
        statement_period_amount: Decimal = Decimal("100.000000"),
        book_period_amount: Decimal = Decimal("100.000000"),
        approved_amount: Decimal = Decimal("100.000000"),
        book_opening_delta: Decimal = Decimal("0"),
    ) -> None:
        self.statement_period_amount = statement_period_amount
        self.book_period_amount = book_period_amount
        self.approved_amount = approved_amount
        self.book_opening_delta = book_opening_delta
        self.queries: list[str] = []

    def execute(self, query: str, parameters: tuple[object, ...]) -> _Rows:
        """Return exact rows only for the source tables production must inspect."""
        self.queries.append(query)
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
                    (
                        "balance-closing-hash",
                        Decimal("1000.000000") + self.statement_period_amount,
                        "KRW",
                        "CRDT",
                    ),
                ]
            )
        if "bank_statement_entry AS entry" in query:
            return _Rows(
                [
                    (
                        "stmt-001",
                        1,
                        self.statement_period_amount,
                        "KRW",
                        "CRDT",
                        False,
                        "sha256:" + "1" * 64,
                    )
                ]
            )
        if "journal_entry_line AS line" in query:
            return _Rows(
                [
                    (
                        "journal-opening",
                        date(2026, 7, 31),
                        datetime(2026, 7, 31, 12, tzinfo=timezone.utc),
                        1,
                        Decimal("1000.000000") + self.book_opening_delta,
                        Decimal("0"),
                        "KRW",
                    ),
                    (
                        "journal-001",
                        date(2026, 8, 15),
                        datetime(2026, 8, 15, 12, tzinfo=timezone.utc),
                        1,
                        self.book_period_amount,
                        Decimal("0"),
                        "KRW",
                    ),
                ]
            )
        if "statement_match_allocation AS allocation" in query:
            return _Rows([("stmt-001", self.approved_amount)])
        if "journal_match_allocation AS allocation" in query:
            return _Rows([("journal-001", self.approved_amount)])
        raise AssertionError(f"unexpected authority query: {query}")


class ReconciliationClosePackageDatabasePopulationTests(unittest.TestCase):
    """Require close evidence to come from immutable PostgreSQL source populations."""

    def test_loader_derives_exact_populations_and_balances_without_caller_projection(self) -> None:
        connection = _AuthorityConnection()
        result = close_package._database_owned_close_projection_evidence(
            connection,
            "tenant-id",
            reconciliation_run_reference="run-001",
        )

        self.assertRegex(result.statement_population_reference, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(result.book_population_reference, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(result.statement_opening_balance, Decimal("1000.000000"))
        self.assertEqual(result.statement_period_movements, Decimal("100.000000"))
        self.assertEqual(result.statement_closing_balance, Decimal("1100.000000"))
        self.assertEqual(result.book_opening_balance, Decimal("1000.000000"))
        self.assertEqual(result.posted_cash_book_movements, Decimal("100.000000"))
        self.assertEqual(result.book_closing_balance, Decimal("1100.000000"))
        self.assertEqual(result.reconciled_book_balance, Decimal("1100.000000"))
        self.assertEqual(result.outstanding_bank_items, Decimal("0"))
        self.assertEqual(result.outstanding_book_items, Decimal("0"))
        self.assertEqual(result.unexplained_difference, Decimal("0"))

        sql = "\n".join(connection.queries)
        self.assertIn("bank_statement_entry AS entry", sql)
        self.assertIn("bank_statement_balance AS balance", sql)
        self.assertIn("journal_entry_line AS line", sql)
        self.assertIn("statement_match_allocation AS allocation", sql)
        self.assertIn("journal_match_allocation AS allocation", sql)

    def test_loader_preserves_bank_side_item_not_yet_posted_to_book(self) -> None:
        result = close_package._database_owned_close_projection_evidence(
            _AuthorityConnection(statement_period_amount=Decimal("150.000000")),
            "tenant-id",
            reconciliation_run_reference="run-001",
        )

        self.assertEqual(result.statement_closing_balance, Decimal("1150.000000"))
        self.assertEqual(result.book_closing_balance, Decimal("1100.000000"))
        self.assertEqual(result.outstanding_book_items, Decimal("50.000000"))
        self.assertEqual(result.outstanding_bank_items, Decimal("0"))
        self.assertEqual(result.unexplained_difference, Decimal("0"))

    def test_loader_preserves_book_side_item_not_yet_seen_by_bank(self) -> None:
        result = close_package._database_owned_close_projection_evidence(
            _AuthorityConnection(book_period_amount=Decimal("150.000000")),
            "tenant-id",
            reconciliation_run_reference="run-001",
        )

        self.assertEqual(result.statement_closing_balance, Decimal("1100.000000"))
        self.assertEqual(result.book_closing_balance, Decimal("1150.000000"))
        self.assertEqual(result.outstanding_book_items, Decimal("0"))
        self.assertEqual(result.outstanding_bank_items, Decimal("50.000000"))
        self.assertEqual(result.unexplained_difference, Decimal("0"))

    def test_loader_rejects_unexplained_opening_difference(self) -> None:
        with self.assertRaisesRegex(ValueError, "database-owned book-to-bank bridge"):
            close_package._database_owned_close_projection_evidence(
                _AuthorityConnection(book_opening_delta=Decimal("1.000000")),
                "tenant-id",
                reconciliation_run_reference="run-001",
            )


if __name__ == "__main__":
    unittest.main()
