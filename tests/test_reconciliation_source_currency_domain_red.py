"""RED contracts for canonical reconciliation source-currency evidence.

Currency participates in both strong-reference verification and the weaker exact-money/date
fallback. It must therefore enter deterministic reconciliation in the same canonical
three-uppercase-letter syntax used by the accounting core, rather than relying on Python
annotations or equality between two malformed values.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    DeterministicMatchPolicy,
    StatementEntryEvidence,
    propose_deterministic_match,
)


class ReconciliationSourceCurrencyDomainRedTests(unittest.TestCase):
    """Reject malformed source currency before it can participate in matching."""

    @staticmethod
    def _statement(**overrides: object) -> StatementEntryEvidence:
        values: dict[str, object] = {
            "statement_entry_reference": "statement-currency-domain-1",
            "provider_reference": "provider-currency-domain-1",
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
            "journal_reference": "journal-currency-domain-1",
            "provider_reference": "provider-currency-domain-1",
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "accounting_date": date(2026, 9, 22),
        }
        values.update(overrides)
        return BookJournalEvidence(**values)  # type: ignore[arg-type]

    def test_statement_rejects_malformed_currency_domain(self) -> None:
        """Statement evidence cannot admit blank, malformed, or non-string currency."""
        for value in ("", "   ", "krw", "KR", "KRWW", 410, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "currency_code must be a three-letter uppercase currency code",
                ):
                    self._statement(currency_code=value)

    def test_journal_rejects_malformed_currency_domain(self) -> None:
        """Book evidence uses the same canonical currency boundary as statement evidence."""
        for value in ("", "\t", "usd", "US", "USDD", 840, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "currency_code must be a three-letter uppercase currency code",
                ):
                    self._journal(currency_code=value)

    def test_valid_currency_still_supports_strong_reference_precedence(self) -> None:
        """The repair must not weaken valid strong-reference matching."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(accounting_date=date(2026, 10, 22)),),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "provider_reference")
        self.assertEqual(
            decision.matched_journal_references,
            ("journal-currency-domain-1",),
        )
        self.assertEqual(decision.allocated_amount, Decimal("1000.00"))

    def test_valid_currency_still_supports_exact_money_date_fallback(self) -> None:
        """Canonical currency continues to participate in the bounded fallback rule."""
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
        self.assertEqual(
            decision.matched_journal_references,
            ("journal-currency-domain-1",),
        )


if __name__ == "__main__":
    unittest.main()
