"""RED contract for aggregate statement-population runtime admission.

Aggregate allocation planning publishes immutable reconciliation evidence from a
statement-side population. The public contract requires an exact built-in tuple
of exact repository-owned ``StatementEntryEvidence``. Mutable containers,
duck-typed members, and caller-behavior-bearing subclasses must fail before they
can participate in reviewable allocation evidence.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest

from accounting_information_platform.allocation import aggregate_allocations
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class _ExplodingOuterTuple(tuple):
    """Expose tuple shape while proving planner iteration would execute caller code."""

    def __iter__(self):
        """Raise if aggregate planning iterates a caller-defined population type."""
        raise RuntimeError("caller-defined outer population iteration executed")


class _DuckStatementEvidence:
    """Expose statement-shaped fields without repository-owned source admission."""

    statement_entry_reference = "stmt-duck"
    amount = Decimal("1000.00")
    currency_code = "KRW"
    credit_debit_code = "DBIT"


class _ExplodingStatementEvidence(StatementEntryEvidence):
    """Prove member subclasses fail before caller-defined attribute behavior runs."""

    explode_amount_reads = False

    def __getattribute__(self, name: str):
        """Raise on amount reads only after canonical construction has completed."""
        if name == "amount" and type(self).explode_amount_reads:
            raise RuntimeError("caller-defined statement behavior executed")
        return super().__getattribute__(name)


class AggregateStatementPopulationDomainRedTests(unittest.TestCase):
    """Require immutable built-in aggregate statement evidence populations."""

    @staticmethod
    def _journal() -> BookJournalEvidence:
        """Return one admitted journal source while statement population shape varies."""
        return BookJournalEvidence(
            journal_reference="journal-a",
            provider_reference="provider-a",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    @staticmethod
    def _statement(reference: str, amount: str) -> StatementEntryEvidence:
        """Build canonical statement evidence for positive population controls."""
        return StatementEntryEvidence(
            statement_entry_reference=reference,
            provider_reference=f"provider-{reference}",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code="KRW",
            credit_debit_code="DBIT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @classmethod
    def _plan(cls, statement_items: object):
        """Plan one exact conserved aggregate while varying only population shape."""
        return aggregate_allocations(
            statement_items=statement_items,  # type: ignore[arg-type]
            journal_evidence=cls._journal(),
            reconciliation_run_reference="run-1",
            tenant_account_reference="tenant-a",
        )

    def test_exact_tuple_population_and_evidence_members_remain_valid(self) -> None:
        """Canonical immutable population keeps aggregate behavior valid."""
        allocations = self._plan(
            (
                self._statement("stmt-001", "400.00"),
                self._statement("stmt-002", "600.00"),
            )
        )
        self.assertEqual(len(allocations), 2)
        self.assertEqual(
            tuple(item.statement_entry_reference for item in allocations),
            ("stmt-001", "stmt-002"),
        )
        self.assertEqual(
            sum((item.allocated_amount for item in allocations), Decimal("0")),
            Decimal("1000.00"),
        )

    def test_mutable_outer_population_fails_closed(self) -> None:
        """A list cannot become the source population for aggregate evidence."""
        with self.assertRaisesRegex(ValueError, "statement_items"):
            self._plan([self._statement("stmt-001", "1000.00")])

    def test_outer_tuple_subclass_fails_before_custom_iteration(self) -> None:
        """Caller-defined outer tuple behavior cannot execute during admission."""
        hostile = _ExplodingOuterTuple((self._statement("stmt-001", "1000.00"),))
        with self.assertRaisesRegex(ValueError, "statement_items"):
            self._plan(hostile)

    def test_duck_typed_statement_member_fails_closed(self) -> None:
        """Attribute-compatible objects cannot manufacture bank statement provenance."""
        with self.assertRaisesRegex(ValueError, "StatementEntryEvidence"):
            self._plan((_DuckStatementEvidence(),))

    def test_statement_subclass_fails_before_custom_attribute_behavior(self) -> None:
        """A statement subclass fails before caller-defined amount reads can execute."""
        statement = _ExplodingStatementEvidence(
            statement_entry_reference="stmt-subclass",
            provider_reference="provider-subclass",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="DBIT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )
        _ExplodingStatementEvidence.explode_amount_reads = True
        with self.assertRaisesRegex(ValueError, "StatementEntryEvidence"):
            self._plan((statement,))


if __name__ == "__main__":
    unittest.main()
