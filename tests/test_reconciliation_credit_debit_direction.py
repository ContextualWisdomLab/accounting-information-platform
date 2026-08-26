"""RED contracts for bank-to-book credit/debit direction compatibility.

ISO 20022 statement amounts are accompanied by a CRDT/DBIT direction. Matching
must preserve that economic direction; equal money and references alone cannot
reconcile an incoming bank movement to an outgoing book movement.
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


class ReconciliationCreditDebitDirectionTests(unittest.TestCase):
    """Require deterministic proposals to preserve bank-movement direction."""

    def _statement(self, *, credit_debit_code: str = "CRDT") -> StatementEntryEvidence:
        return StatementEntryEvidence(
            statement_entry_reference="statement-direction-1",
            provider_reference="provider-direction-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("25000.00"),
            currency_code="KRW",
            credit_debit_code=credit_debit_code,
            booking_date=date(2026, 8, 24),
            value_date=date(2026, 8, 24),
        )

    def _journal(
        self,
        *,
        provider_reference: str | None = "provider-direction-1",
        credit_debit_code: str = "CRDT",
    ) -> BookJournalEvidence:
        return BookJournalEvidence(
            journal_reference="journal-direction-1",
            provider_reference=provider_reference,
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("25000.00"),
            currency_code="KRW",
            credit_debit_code=credit_debit_code,
            accounting_date=date(2026, 8, 24),
        )

    def test_strong_identity_with_opposite_direction_fails_closed(self) -> None:
        """A stable reference cannot override opposite CRDT/DBIT evidence."""
        decision = propose_deterministic_match(
            self._statement(credit_debit_code="CRDT"),
            (self._journal(credit_debit_code="DBIT"),),
            DeterministicMatchPolicy(date_window_days=2),
        )

        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "direction_mismatch")
        self.assertEqual(decision.matched_journal_references, ())
        self.assertEqual(decision.allocated_amount, Decimal("0"))
        self.assertEqual(
            decision.next_action,
            "Verify whether the bank movement is a credit or debit before recording a reconciliation decision.",
        )

    def test_weak_exact_money_rule_never_matches_opposite_direction(self) -> None:
        """Exact money/date evidence remains insufficient when movement direction conflicts."""
        decision = propose_deterministic_match(
            self._statement(credit_debit_code="CRDT"),
            (
                self._journal(
                    provider_reference=None,
                    credit_debit_code="DBIT",
                ),
            ),
            DeterministicMatchPolicy(date_window_days=2),
        )

        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "direction_mismatch")
        self.assertEqual(decision.matched_journal_references, ())
        self.assertEqual(decision.allocated_amount, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
