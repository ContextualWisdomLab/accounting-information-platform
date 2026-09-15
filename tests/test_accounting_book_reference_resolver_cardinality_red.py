"""RED coverage for fail-closed Accounting Book durable-reference resolution."""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from accounting_information_platform.persistence import (
    AccountingValidationError,
    PostgresPostingLedger,
)


class _BookRowsCursor:
    """Expose one, many, or no scripted Accounting Book rows to the resolver."""

    def __init__(self, rows: list[tuple[UUID, str]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[UUID, str] | None:
        """Model the current first-row cursor behavior without hiding ambiguity."""
        return self._rows[0] if self._rows else None

    def fetchmany(self, size: int = 1) -> list[tuple[UUID, str]]:
        """Support a future bounded cardinality read without pinning its implementation."""
        return self._rows[:size]

    def fetchall(self) -> list[tuple[UUID, str]]:
        """Support an equivalent full cardinality read."""
        return list(self._rows)


class _BookRowsConnection:
    """Return scripted book rows for one catalog lookup and record the SQL boundary."""

    def __init__(self, rows: list[tuple[UUID, str]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, object | None]] = []

    def execute(self, statement: str, params: object | None = None) -> _BookRowsCursor:
        """Record the lookup and return a cursor containing the scripted matches."""
        self.executed.append((statement, params))
        return _BookRowsCursor(self.rows)


class AccountingBookReferenceResolverCardinalityRedTests(unittest.TestCase):
    """Require public book-reference resolution to distinguish zero, one, and many matches."""

    def setUp(self) -> None:
        """Bind one ledger adapter without opening a real database connection."""
        self.ledger = PostgresPostingLedger(
            "postgresql://unused",
            tenant_reference="urn:cwl:tenant:book-cardinality",
        )
        self.tenant_id = uuid4()
        self.legal_entity_id = uuid4()
        self.book_reference = "urn:cwl:accounting_book:book-cardinality"

    def test_zero_effective_book_matches_fail_closed(self) -> None:
        """A missing durable reference must not resolve to a synthetic or default book."""
        connection = _BookRowsConnection([])

        with self.assertRaises(AccountingValidationError):
            self.ledger._require_book_for_close(
                connection,
                self.tenant_id,
                self.legal_entity_id,
                self.book_reference,
                "the accounting-book read",
            )

    def test_exactly_one_effective_book_match_succeeds(self) -> None:
        """One effective durable-reference match resolves its immutable Entity key."""
        book_id = uuid4()
        connection = _BookRowsConnection([(book_id, "KRW")])

        self.assertEqual(
            self.ledger._require_book_for_close(
                connection,
                self.tenant_id,
                self.legal_entity_id,
                self.book_reference,
                "the accounting-book read",
            ),
            (book_id, "KRW"),
        )

    def test_multiple_effective_book_matches_fail_closed(self) -> None:
        """Ambiguous durable references must never collapse to an arbitrary first row."""
        connection = _BookRowsConnection(
            [
                (uuid4(), "KRW"),
                (uuid4(), "KRW"),
            ]
        )

        with self.assertRaises(AccountingValidationError):
            self.ledger._require_book_for_close(
                connection,
                self.tenant_id,
                self.legal_entity_id,
                self.book_reference,
                "the accounting-book read",
            )


if __name__ == "__main__":
    unittest.main()
