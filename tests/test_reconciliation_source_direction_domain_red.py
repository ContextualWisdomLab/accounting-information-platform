"""RED contracts for reconciliation source movement direction evidence.

Credit/debit direction is runtime accounting evidence, not a trusted Python annotation.
Malformed or unhashable values must fail with the reconciliation domain's ValueError before
strong-reference precedence or weaker exact-money/date matching can consume them.
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


class _UnhashableDirection(str):
    """Exercise runtime type admission before hashed membership."""

    __hash__ = None  # type: ignore[assignment]


class ReconciliationSourceDirectionDomainRedTests(unittest.TestCase):
    """Require canonical CRDT/DBIT direction evidence before deterministic matching."""

    @staticmethod
    def _statement(**overrides: object) -> StatementEntryEvidence:
        values: dict[str, object] = {
            "statement_entry_reference": "statement-direction-domain-1",
            "provider_reference": "provider-direction-domain-1",
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
            "journal_reference": "journal-direction-domain-1",
            "provider_reference": "provider-direction-domain-1",
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "accounting_date": date(2026, 9, 22),
        }
        values.update(overrides)
        return BookJournalEvidence(**values)  # type: ignore[arg-type]

    def test_statement_rejects_malformed_direction_with_domain_error(self) -> None:
        """Malformed statement direction never rides through a strong reference match."""
        for value in ("crdt", "", None, 1, [], {}, _UnhashableDirection("CRDT")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "credit_debit_code must be CRDT or DBIT"):
                    self._statement(credit_debit_code=value)

    def test_journal_rejects_malformed_direction_with_domain_error(self) -> None:
        """Malformed book direction fails before candidate comparison, including unhashable values."""
        for value in ("dbit", "", None, 1, [], {}, _UnhashableDirection("DBIT")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "credit_debit_code must be CRDT or DBIT"):
                    self._journal(credit_debit_code=value)

    def test_valid_credit_direction_preserves_strong_reference_precedence(self) -> None:
        """Canonical CRDT evidence keeps the existing strong-reference behavior."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(accounting_date=date(2026, 10, 22)),),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "provider_reference")
        self.assertEqual(decision.matched_journal_references, ("journal-direction-domain-1",))

    def test_valid_debit_direction_preserves_bounded_date_fallback(self) -> None:
        """Canonical DBIT evidence remains eligible for the weaker exact-money/date rule."""
        statement = self._statement(
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
            credit_debit_code="DBIT",
        )
        journal = self._journal(
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
            credit_debit_code="DBIT",
        )

        decision = propose_deterministic_match(
            statement,
            (journal,),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "exact_money_bounded_date")
        self.assertEqual(decision.matched_journal_references, ("journal-direction-domain-1",))


if __name__ == "__main__":
    unittest.main()
