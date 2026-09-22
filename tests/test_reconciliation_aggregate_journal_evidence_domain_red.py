"""RED contract for canonical aggregate journal-side evidence admission.

Aggregate allocation planning must consume repository-owned posted-journal
evidence, not caller-supplied journal totals, identities, and currencies that can
be made mutually consistent without proving they came from one admitted journal
source. The statement side is held at canonical ``StatementEntryEvidence`` while
journal provenance varies.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from inspect import Parameter, signature
from typing import get_overloads, get_type_hints
import unittest

from accounting_information_platform.allocation import aggregate_allocations
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class _DuckAggregateJournal:
    """Expose journal-shaped fields without passing repository evidence admission."""

    journal_reference = "journal-duck"
    amount = Decimal("1000.00")
    currency_code = "KRW"


class _ExplodingAggregateJournal(BookJournalEvidence):
    """Prove subclass-defined attribute behavior cannot enter aggregate planning."""

    explode_amount_reads = False

    def __getattribute__(self, name: str):
        """Raise on amount reads only after the canonical constructor has run."""
        if name == "amount" and type(self).explode_amount_reads:
            raise RuntimeError("caller-defined aggregate journal behavior executed")
        return super().__getattribute__(name)


class AggregateJournalEvidenceDomainRedTests(unittest.TestCase):
    """Require one exact ``BookJournalEvidence`` as aggregate book-side provenance."""

    @staticmethod
    def _journal() -> BookJournalEvidence:
        """Build one canonical posted-journal evidence control."""
        return BookJournalEvidence(
            journal_reference="journal-001",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    @staticmethod
    def _statement() -> StatementEntryEvidence:
        """Hold the statement side at one canonical admitted source."""
        return StatementEntryEvidence(
            statement_entry_reference="statement-001",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="CRDT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @classmethod
    def _plan(cls, journal_evidence: object):
        """Plan one conserved aggregate while varying only book-side object provenance."""
        return aggregate_allocations(
            statement_items=(cls._statement(),),
            journal_evidence=journal_evidence,  # type: ignore[arg-type]
            reconciliation_run_reference="run-001",
            tenant_account_reference="tenant-001",
        )

    def test_exact_book_journal_evidence_drives_aggregate_provenance(self) -> None:
        """Canonical evidence supplies journal identity, money, and currency atomically."""
        allocations = self._plan(self._journal())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].journal_reference, "journal-001")
        self.assertEqual(allocations[0].currency_code, "KRW")
        self.assertEqual(allocations[0].allocated_amount, Decimal("1000.00"))

    def test_scalar_only_journal_provenance_fails_closed(self) -> None:
        """Mutually consistent caller scalars cannot substitute for admitted journal evidence."""
        with self.assertRaisesRegex(ValueError, "BookJournalEvidence"):
            aggregate_allocations(
                statement_items=(self._statement(),),
                journal_total=Decimal("1000.00"),
                journal_reference="journal-001",
                currency_code="KRW",
                reconciliation_run_reference="run-001",
                tenant_account_reference="tenant-001",
            )

    def test_duck_typed_journal_evidence_fails_closed(self) -> None:
        """Attribute-compatible objects cannot manufacture posted-journal provenance."""
        with self.assertRaisesRegex(ValueError, "BookJournalEvidence"):
            self._plan(_DuckAggregateJournal())

    def test_journal_subclass_fails_before_custom_attribute_behavior(self) -> None:
        """A subclass fails before caller-defined amount reads can execute."""
        journal = _ExplodingAggregateJournal(
            journal_reference="journal-subclass",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )
        _ExplodingAggregateJournal.explode_amount_reads = True
        with self.assertRaisesRegex(ValueError, "BookJournalEvidence"):
            self._plan(journal)

    def test_public_overloads_require_book_journal_evidence(self) -> None:
        """Typed callers cannot present independent scalar journal provenance."""
        overloads = get_overloads(aggregate_allocations)
        self.assertGreaterEqual(len(overloads), 2)
        for overload_variant in overloads:
            parameters = signature(overload_variant).parameters
            type_hints = get_type_hints(overload_variant)
            self.assertIs(parameters["journal_evidence"].default, Parameter.empty)
            self.assertIs(type_hints["journal_evidence"], BookJournalEvidence)
            for legacy_name in ("journal_total", "journal_reference", "currency_code"):
                self.assertNotIn(legacy_name, parameters)


if __name__ == "__main__":
    unittest.main()
