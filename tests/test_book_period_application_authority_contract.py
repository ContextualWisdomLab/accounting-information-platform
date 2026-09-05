"""Application-side contracts for accounting-book period authority.

Migration 0034 deliberately leaves unsupported non-open book/period pairs absent.
The persistence adapter must therefore treat a missing accounting_book_period_control
row as missing authority rather than rebuilding or inferring it from fiscal_period.
"""

from __future__ import annotations

import inspect
import unittest

from accounting_information_platform.persistence import PostgresPostingLedger


class BookPeriodApplicationAuthorityContractTests(unittest.TestCase):
    """Keep application helpers aligned with the book-scoped PostgreSQL authority."""

    def test_close_lock_is_read_lock_fail_closed_only(self) -> None:
        source = inspect.getsource(PostgresPostingLedger._lock_book_period)

        self.assertNotIn(
            "INSERT INTO accounting_core.accounting_book_period_control",
            source,
            "close runtime must not manufacture book-period authority",
        )
        self.assertNotIn(
            "fiscal_period.period_status_code",
            source,
            "close runtime must not copy the shared calendar projection into book authority",
        )
        self.assertIn("FOR UPDATE OF accounting_book_period_control", source)

    def test_adjusting_state_has_no_shared_calendar_fallback(self) -> None:
        source = inspect.getsource(PostgresPostingLedger._load_book_period_state)

        self.assertNotIn("COALESCE(", source)
        self.assertNotIn("LEFT JOIN accounting_core.accounting_book_period_control", source)
        self.assertIn("JOIN accounting_core.accounting_book_period_control", source)
        self.assertIn("accounting_book_period_control.period_status_code", source)

    def test_open_posting_has_no_shared_calendar_fallback(self) -> None:
        source = inspect.getsource(PostgresPostingLedger._require_open_book_period_bounds)

        self.assertNotIn("COALESCE(", source)
        self.assertNotIn("LEFT JOIN accounting_core.accounting_book_period_control", source)
        self.assertIn("JOIN accounting_core.accounting_book_period_control", source)
        self.assertIn("accounting_book_period_control.period_status_code", source)


if __name__ == "__main__":
    unittest.main()
