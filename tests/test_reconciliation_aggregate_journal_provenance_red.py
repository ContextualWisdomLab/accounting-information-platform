"""RED contract for explicit aggregate journal provenance.

Aggregate allocation proposals are reviewable reconciliation evidence. They must
not manufacture a journal identity or currency when source bindings are absent,
and mutually consistent caller scalars are not a substitute for one admitted
posted-journal evidence object. Exact conserved statement money is insufficient
provenance by itself.
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


class AggregateJournalProvenanceRedTests(unittest.TestCase):
    """Require canonical journal evidence on aggregate proposals."""

    @staticmethod
    def _journal() -> BookJournalEvidence:
        """Return one canonical book-side source for aggregate planning."""
        return BookJournalEvidence(
            journal_reference="journal-001",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="USD",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    @staticmethod
    def _statement() -> StatementEntryEvidence:
        """Return one canonical statement source so only book provenance varies."""
        return StatementEntryEvidence(
            statement_entry_reference="statement-001",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="USD",
            credit_debit_code="CRDT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @classmethod
    def _plan(cls, **overrides: object):
        """Plan one conserved aggregate while varying only journal provenance."""
        values: dict[str, object] = {
            "statement_items": (cls._statement(),),
            "journal_evidence": cls._journal(),
            "reconciliation_run_reference": "run-001",
            "tenant_account_reference": "tenant-001",
        }
        values.update(overrides)
        return aggregate_allocations(**values)  # type: ignore[arg-type]

    def test_canonical_journal_evidence_remains_valid(self) -> None:
        """One admitted journal source preserves exact aggregate allocation behavior."""
        allocations = self._plan()
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].journal_reference, "journal-001")
        self.assertEqual(allocations[0].currency_code, "USD")
        self.assertEqual(allocations[0].allocated_amount, Decimal("1000.00"))

    def test_omitted_journal_evidence_fails_closed(self) -> None:
        """The planner cannot invent book-side source provenance."""
        with self.assertRaisesRegex(ValueError, "BookJournalEvidence"):
            self._plan(journal_evidence=None)

    def test_legacy_scalar_provenance_fails_closed(self) -> None:
        """Journal total, identity, and currency scalars cannot replace admitted evidence."""
        with self.assertRaisesRegex(ValueError, "BookJournalEvidence"):
            aggregate_allocations(
                statement_items=(self._statement(),),
                journal_total=Decimal("1000.00"),
                journal_reference="journal-001",
                currency_code="USD",
                reconciliation_run_reference="run-001",
                tenant_account_reference="tenant-001",
            )

    def test_public_overloads_require_book_journal_evidence(self) -> None:
        """Typed callers see one source object rather than independent provenance scalars."""
        overloads = get_overloads(aggregate_allocations)
        self.assertGreaterEqual(len(overloads), 2)
        for overload_variant in overloads:
            parameters = signature(overload_variant).parameters
            type_hints = get_type_hints(overload_variant)
            self.assertIs(parameters["journal_evidence"].default, Parameter.empty)
            self.assertIs(type_hints["journal_evidence"], BookJournalEvidence)
            for field_name in ("journal_total", "journal_reference", "currency_code"):
                self.assertNotIn(field_name, parameters)


if __name__ == "__main__":
    unittest.main()
