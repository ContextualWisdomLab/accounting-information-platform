"""RED contract for immutable split candidate population admission.

Split allocation planning consumes a bounded snapshot of posted-journal evidence.
The population container itself must therefore be an exact built-in tuple before
iteration. Mutable containers and caller-defined iterables must fail closed
without executing custom iteration behavior, while exact repository-owned
``BookJournalEvidence`` members in an exact tuple remain valid.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.allocation import propose_split_allocations
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class _ExplodingCandidatePopulation:
    """Raise if split planning executes caller-owned population iteration."""

    def __iter__(self):
        """Prove non-canonical populations are rejected before iteration."""
        raise RuntimeError("caller-defined candidate iteration executed")


class SplitCandidatePopulationDomainRedTests(unittest.TestCase):
    """Require an exact immutable tuple before split population iteration."""

    @staticmethod
    def _statement() -> StatementEntryEvidence:
        """Build one canonical bank-statement evidence control."""
        return StatementEntryEvidence(
            statement_entry_reference="stmt-001",
            provider_reference="provider-statement-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            booking_date=date(2026, 9, 1),
            value_date=date(2026, 9, 1),
        )

    @staticmethod
    def _journal() -> BookJournalEvidence:
        """Build one canonical posted-journal evidence control."""
        return BookJournalEvidence(
            journal_reference="journal-a",
            provider_reference="provider-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 1),
        )

    @staticmethod
    def _plan(candidate_journals: object):
        """Hold accounting facts constant while varying only population container type."""
        return propose_split_allocations(
            statement_evidence=SplitCandidatePopulationDomainRedTests._statement(),
            candidate_journals=candidate_journals,  # type: ignore[arg-type]
            reconciliation_run_reference="run-1",
            tenant_account_reference="tenant-a",
        )

    def test_exact_builtin_tuple_remains_valid(self) -> None:
        """An exact tuple of canonical journal evidence remains accepted."""
        allocations = self._plan((self._journal(),))
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].journal_reference, "journal-a")
        self.assertEqual(allocations[0].allocated_amount, Decimal("1000.00"))
        self.assertEqual(allocations[0].currency_code, "KRW")

    def test_mutable_candidate_population_fails_closed(self) -> None:
        """A list cannot become reviewable split evidence through an implicit snapshot."""
        with self.assertRaisesRegex(ValueError, "candidate_journals.*built-in tuple"):
            self._plan([self._journal()])

    def test_custom_iterable_fails_before_caller_iteration(self) -> None:
        """Caller-defined iteration cannot execute before population admission."""
        with self.assertRaisesRegex(ValueError, "candidate_journals.*built-in tuple"):
            self._plan(_ExplodingCandidatePopulation())


if __name__ == "__main__":
    unittest.main()
