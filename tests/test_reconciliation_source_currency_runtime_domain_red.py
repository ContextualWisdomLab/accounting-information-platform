"""RED contracts for reconciliation source-currency runtime domains.

Currency syntax alone is insufficient for durable reconciliation evidence when a
caller can supply a ``str`` subclass with custom comparison behavior. Admission
must reject subclasses before matching can use currency equality while preserving
ordinary canonical built-in currency strings.
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


class _ExplodingCurrencyStr(str):
    """Expose caller-controlled equality if a currency subclass reaches matching."""

    def __eq__(self, other: object) -> bool:
        """Fail if reconciliation executes caller equality semantics."""
        raise RuntimeError("caller-controlled currency equality must not execute")

    def __ne__(self, other: object) -> bool:
        """Fail if reconciliation executes caller inequality semantics."""
        raise RuntimeError("caller-controlled currency inequality must not execute")


class ReconciliationSourceCurrencyRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in currency strings on statement and book evidence."""

    @staticmethod
    def _statement(currency_code: object = "USD") -> StatementEntryEvidence:
        """Build otherwise-valid statement evidence while varying only currency."""
        return StatementEntryEvidence(
            statement_entry_reference="statement-currency-domain-1",
            provider_reference="provider-currency-domain-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code=currency_code,  # type: ignore[arg-type]
            credit_debit_code="CRDT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @staticmethod
    def _journal(currency_code: object = "USD") -> BookJournalEvidence:
        """Build otherwise-valid book evidence while varying only currency."""
        return BookJournalEvidence(
            journal_reference="journal-currency-domain-1",
            provider_reference="provider-currency-domain-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code=currency_code,  # type: ignore[arg-type]
            credit_debit_code="CRDT",
            accounting_date=date(2026, 9, 22),
        )

    def test_statement_currency_subclass_fails_at_evidence_admission(self) -> None:
        """Statement currency cannot carry caller-defined string behavior."""
        with self.assertRaisesRegex(
            ValueError,
            "currency_code must be a three-letter uppercase currency code",
        ):
            self._statement(_ExplodingCurrencyStr("USD"))

    def test_book_currency_subclass_fails_at_evidence_admission(self) -> None:
        """Book currency cannot carry caller-defined comparison behavior."""
        with self.assertRaisesRegex(
            ValueError,
            "currency_code must be a three-letter uppercase currency code",
        ):
            self._journal(_ExplodingCurrencyStr("USD"))

    def test_builtin_currency_strings_preserve_deterministic_match(self) -> None:
        """Canonical built-in currency strings retain existing matching semantics."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(),),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "provider_reference")
        self.assertEqual(
            decision.matched_journal_references,
            ("journal-currency-domain-1",),
        )
        self.assertEqual(decision.allocated_amount, Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
