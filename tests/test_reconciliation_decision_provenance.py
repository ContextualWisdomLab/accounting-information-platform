"""RED contract for reconciliation-decision source provenance.

A reviewable deterministic match or abstention must carry the immutable statement
entry identity that produced it. Call-site context is not durable audit evidence;
the decision object itself must remain attributable when it is logged, exported,
or later persisted by the authoritative reconciliation workflow.
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


class ReconciliationDecisionProvenanceTests(unittest.TestCase):
    """Require every proposal decision to retain its source statement identity."""

    def _statement(self) -> StatementEntryEvidence:
        return StatementEntryEvidence(
            statement_entry_reference="statement-provenance-1",
            provider_reference="provider-provenance-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("125000.00"),
            currency_code="KRW",
            credit_debit_code="CRDT",
            booking_date=date(2026, 8, 25),
            value_date=date(2026, 8, 25),
        )

    def _journal(self, *, credit_debit_code: str = "CRDT") -> BookJournalEvidence:
        return BookJournalEvidence(
            journal_reference="journal-provenance-1",
            provider_reference="provider-provenance-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("125000.00"),
            currency_code="KRW",
            credit_debit_code=credit_debit_code,
            accounting_date=date(2026, 8, 25),
        )

    def test_match_decision_retains_statement_entry_identity(self) -> None:
        """A positive proposal remains attributable to immutable statement evidence."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(),),
            DeterministicMatchPolicy(date_window_days=2),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.statement_entry_reference, "statement-provenance-1")
        self.assertEqual(decision.matched_journal_references, ("journal-provenance-1",))

    def test_abstention_retains_statement_entry_identity(self) -> None:
        """An exception remains attributable even when no monetary evidence is consumed."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(credit_debit_code="DBIT"),),
            DeterministicMatchPolicy(date_window_days=2),
        )

        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "direction_mismatch")
        self.assertEqual(decision.statement_entry_reference, "statement-provenance-1")
        self.assertEqual(decision.matched_journal_references, ())
        self.assertEqual(decision.allocated_amount, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
