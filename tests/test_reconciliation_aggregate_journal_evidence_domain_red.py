"""RED contract for canonical aggregate journal-side evidence admission.

Aggregate allocation planning must consume repository-owned posted-journal
evidence, not caller-supplied journal totals, identities, and currencies that can
be made mutually consistent without proving they came from one admitted journal
source. The statement population and exact monetary conservation remain unchanged.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from inspect import Parameter, signature
from typing import get_overloads, get_type_hints
import unittest

from accounting_information_platform.allocation import aggregate_allocations
from accounting_information_platform.reconciliation import BookJournalEvidence


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


class _ForgedAggregateJournalAmount(Decimal):
    """Pretend a mismatched journal amount is equal during conservation comparison."""

    def __ne__(self, other: object) -> bool:
        """Return false so a mismatched statement total appears conserved."""
        return False


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
    def _plan(journal_evidence: object, **overrides: object):
        """Plan one conserved aggregate while varying only book-side object provenance."""
        values: dict[str, object] = {
            "statement_items": (("statement-001", Decimal("1000.00")),),
            "journal_evidence": journal_evidence,
            "reconciliation_run_reference": "run-001",
            "tenant_account_reference": "tenant-001",
        }
        values.update(overrides)
        return aggregate_allocations(**values)  # type: ignore[arg-type]

    def test_exact_book_journal_evidence_drives_aggregate_provenance(self) -> None:
        """Canonical evidence supplies journal identity, money, and currency atomically."""
        allocations = self._plan(self._journal())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].journal_reference, "journal-001")
        self.assertEqual(allocations[0].currency_code, "KRW")
        self.assertEqual(allocations[0].allocated_amount, Decimal("1000.00"))

    def test_legacy_scalars_fail_after_exact_evidence_admission(self) -> None:
        """Legacy scalar keywords cannot coexist with canonical journal evidence."""
        with self.assertRaisesRegex(ValueError, "not accepted"):
            self._plan(
                self._journal(),
                journal_total=Decimal("1000.00"),
                journal_reference="journal-001",
                currency_code="KRW",
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

    def test_tampered_journal_amount_is_revalidated_before_conservation(self) -> None:
        """Frozen evidence cannot bypass exact-money admission after construction."""
        journal = self._journal()
        object.__setattr__(journal, "amount", _ForgedAggregateJournalAmount("999.00"))
        with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
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