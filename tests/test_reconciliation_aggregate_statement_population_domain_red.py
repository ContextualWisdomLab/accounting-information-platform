"""RED contract for aggregate statement-population runtime admission.

Aggregate allocation planning publishes immutable reconciliation evidence from a
statement-side population. The public contract names that population as an
immutable tuple of exact ``(statement_entry_reference, Decimal)`` tuples. Mutable
or caller-behavior-bearing containers must fail before iteration/destructuring can
participate in reviewable allocation evidence.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.allocation import aggregate_allocations


class _ExplodingOuterTuple(tuple):
    """Expose tuple shape while proving planner iteration would execute caller code."""

    def __iter__(self):
        """Raise if aggregate planning iterates a caller-defined population type."""
        raise RuntimeError("caller-defined outer population iteration executed")


class _ExplodingStatementPair(tuple):
    """Expose pair shape while proving destructuring would execute caller code."""

    def __iter__(self):
        """Raise if aggregate planning destructures a caller-defined pair type."""
        raise RuntimeError("caller-defined statement pair iteration executed")


class AggregateStatementPopulationDomainRedTests(unittest.TestCase):
    """Require immutable built-in aggregate statement population shapes."""

    @staticmethod
    def _plan(statement_items: object):
        """Plan one exact conserved aggregate while varying only population shape."""
        return aggregate_allocations(
            statement_items=statement_items,  # type: ignore[arg-type]
            journal_total=Decimal("1000.00"),
            reconciliation_run_reference="run-1",
            tenant_account_reference="tenant-a",
            journal_reference="journal-a",
            currency_code="KRW",
        )

    def test_exact_tuple_population_and_pairs_remain_valid(self) -> None:
        """Canonical immutable built-in population keeps aggregate behavior valid."""
        allocations = self._plan((("stmt-001", Decimal("400.00")), ("stmt-002", Decimal("600.00"))))
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
            self._plan([("stmt-001", Decimal("1000.00"))])

    def test_mutable_inner_pair_fails_closed(self) -> None:
        """A list pair cannot become one immutable statement allocation source."""
        with self.assertRaisesRegex(ValueError, "statement item"):
            self._plan((["stmt-001", Decimal("1000.00")],))

    def test_outer_tuple_subclass_fails_before_custom_iteration(self) -> None:
        """Caller-defined outer tuple behavior cannot execute during admission."""
        hostile = _ExplodingOuterTuple((("stmt-001", Decimal("1000.00")),))
        with self.assertRaisesRegex(ValueError, "statement_items"):
            self._plan(hostile)

    def test_inner_tuple_subclass_fails_before_custom_iteration(self) -> None:
        """Caller-defined pair iteration cannot execute during admission."""
        hostile_pair = _ExplodingStatementPair(("stmt-001", Decimal("1000.00")))
        with self.assertRaisesRegex(ValueError, "statement item"):
            self._plan((hostile_pair,))


if __name__ == "__main__":
    unittest.main()
