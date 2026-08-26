"""RED contract for unmatched strong bank-reconciliation identity evidence."""

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


class StrongReferenceNoCandidateTests(unittest.TestCase):
    """Keep a missing strong-identity candidate distinct from ambiguity."""

    def test_present_strong_reference_with_zero_matching_candidates_is_no_candidate(self) -> None:
        """Do not report competing candidates when no journal carries the strong identity."""
        statement = StatementEntryEvidence(
            statement_entry_reference="statement-entry-strong-miss",
            provider_reference="provider-reference-expected",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("25000.00"),
            currency_code="KRW",
            booking_date=date(2026, 8, 24),
            value_date=date(2026, 8, 24),
        )
        weak_money_date_candidate = BookJournalEvidence(
            journal_reference="journal-unrelated-provider",
            provider_reference="provider-reference-other",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("25000.00"),
            currency_code="KRW",
            accounting_date=date(2026, 8, 24),
        )

        decision = propose_deterministic_match(
            statement,
            (weak_money_date_candidate,),
            DeterministicMatchPolicy(date_window_days=2),
        )

        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "no_candidate")
        self.assertEqual(decision.matched_journal_references, ())
        self.assertEqual(decision.allocated_amount, Decimal("0"))
        self.assertEqual(
            decision.next_action,
            "Review unmatched statement evidence and create an authorized exception or adjusting-journal proposal if required.",
        )


if __name__ == "__main__":
    unittest.main()
