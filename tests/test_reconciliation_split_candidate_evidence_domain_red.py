"""RED contract for repository-owned split candidate evidence admission.

Split allocation planning consumes posted-journal evidence, not arbitrary Python
objects that happen to expose journal-like attributes. A caller-controlled
object or ``BookJournalEvidence`` subclass must not bypass the source-evidence
constructor or execute custom attribute behavior while becoming reviewable
allocation evidence. Exact repository-owned ``BookJournalEvidence`` remains the
positive control.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.allocation import propose_split_allocations
from accounting_information_platform.reconciliation import BookJournalEvidence


class _DuckJournalCandidate:
    """Expose allocation-shaped attributes without repository evidence admission."""

    journal_reference = "journal-duck"
    amount = Decimal("1000.00")
    currency_code = "KRW"


class _ExplodingJournalCandidate(BookJournalEvidence):
    """Prove subclass-defined attribute behavior cannot enter split planning."""

    explode_currency_reads = False

    def __getattribute__(self, name: str):
        """Raise on currency reads only after the canonical constructor has run."""
        if name == "currency_code" and type(self).explode_currency_reads:
            raise RuntimeError("caller-defined candidate behavior executed")
        return super().__getattribute__(name)


class SplitCandidateEvidenceDomainRedTests(unittest.TestCase):
    """Require split planning to consume exact repository-owned journal evidence."""

    @staticmethod
    def _journal(reference: str = "journal-a") -> BookJournalEvidence:
        """Build one canonical posted-journal evidence control."""
        return BookJournalEvidence(
            journal_reference=reference,
            provider_reference="provider-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 1),
        )

    def _plan(self, candidate: object):
        """Plan one exact conserved split while varying only candidate object domain."""
        return propose_split_allocations(
            statement_entry_reference="stmt-001",
            statement_amount=Decimal("1000.00"),
            candidate_journals=(candidate,),
            reconciliation_run_reference="run-1",
            tenant_account_reference="tenant-a",
        )

    def test_exact_book_journal_evidence_remains_valid(self) -> None:
        """Repository-owned posted-journal evidence remains a valid split source."""
        allocations = self._plan(self._journal())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].journal_reference, "journal-a")
        self.assertEqual(allocations[0].allocated_amount, Decimal("1000.00"))
        self.assertEqual(allocations[0].currency_code, "KRW")

    def test_duck_typed_journal_candidate_fails_closed(self) -> None:
        """Attribute-compatible objects cannot manufacture posted-journal evidence."""
        with self.assertRaisesRegex(ValueError, "BookJournalEvidence"):
            self._plan(_DuckJournalCandidate())

    def test_book_journal_subclass_fails_before_custom_attribute_behavior(self) -> None:
        """A subclass must fail before caller-defined evidence reads can execute."""
        candidate = _ExplodingJournalCandidate(
            journal_reference="journal-subclass",
            provider_reference="provider-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 1),
        )
        _ExplodingJournalCandidate.explode_currency_reads = True
        with self.assertRaisesRegex(ValueError, "BookJournalEvidence"):
            self._plan(candidate)


if __name__ == "__main__":
    unittest.main()
