"""RED contracts for reconciliation source-date evidence.

Source dates are accounting/reconciliation evidence rather than trusted Python annotations.
A strong reference must not allow malformed booking/value/accounting dates to become part of a
reviewable proposal, and the bounded-date fallback must fail with a domain error rather than a
raw subtraction TypeError.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal

from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    DeterministicMatchPolicy,
    StatementEntryEvidence,
    propose_deterministic_match,
)


class ReconciliationSourceDateDomainRedTests(unittest.TestCase):
    """Require exact calendar-date source evidence before deterministic matching."""

    @staticmethod
    def _statement(**overrides: object) -> StatementEntryEvidence:
        values: dict[str, object] = {
            "statement_entry_reference": "statement-date-domain-1",
            "provider_reference": "provider-date-domain-1",
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "booking_date": date(2026, 9, 22),
            "value_date": date(2026, 9, 22),
        }
        values.update(overrides)
        return StatementEntryEvidence(**values)  # type: ignore[arg-type]

    @staticmethod
    def _journal(**overrides: object) -> BookJournalEvidence:
        values: dict[str, object] = {
            "journal_reference": "journal-date-domain-1",
            "provider_reference": "provider-date-domain-1",
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "accounting_date": date(2026, 9, 22),
        }
        values.update(overrides)
        return BookJournalEvidence(**values)  # type: ignore[arg-type]

    def test_statement_rejects_malformed_booking_date(self) -> None:
        """Booking evidence is an exact date even when a strong reference would otherwise match."""
        for value in ("2026-09-22", datetime(2026, 9, 22), 20260922, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "booking_date must be a date"):
                    self._statement(booking_date=value)

    def test_statement_rejects_malformed_value_date(self) -> None:
        """Value-date evidence cannot ride through a matching path merely because it is not compared."""
        for value in ("2026-09-22", datetime(2026, 9, 22), 20260922, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "value_date must be a date"):
                    self._statement(value_date=value)

    def test_journal_rejects_malformed_accounting_date(self) -> None:
        """Book accounting-date evidence must be a calendar date before matching."""
        for value in ("2026-09-22", datetime(2026, 9, 22), 20260922, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "accounting_date must be a date"):
                    self._journal(accounting_date=value)

    def test_valid_dates_preserve_strong_reference_precedence(self) -> None:
        """Valid calendar dates do not weaken strong-reference matching."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(accounting_date=date(2026, 10, 22)),),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "provider_reference")
        self.assertEqual(decision.matched_journal_references, ("journal-date-domain-1",))

    def test_valid_dates_preserve_bounded_date_fallback(self) -> None:
        """Exact calendar dates continue to drive the bounded fallback window."""
        statement = self._statement(
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
        )
        journal = self._journal(
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
        )

        decision = propose_deterministic_match(
            statement,
            (journal,),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "exact_money_bounded_date")
        self.assertEqual(decision.matched_journal_references, ("journal-date-domain-1",))


if __name__ == "__main__":
    unittest.main()
