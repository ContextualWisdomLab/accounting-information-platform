"""RED contract for explicit aggregate journal provenance.

Aggregate allocation proposals are reviewable reconciliation evidence. They must
not manufacture a journal identity or currency when the caller omits those
bindings. Exact conserved statement money is insufficient provenance by itself.
"""

from __future__ import annotations

from decimal import Decimal
import unittest

from accounting_information_platform.allocation import aggregate_allocations


class AggregateJournalProvenanceRedTests(unittest.TestCase):
    """Require explicit journal identity and currency on aggregate proposals."""

    @staticmethod
    def _plan(**overrides: object):
        """Plan one conserved aggregate while varying only journal provenance."""
        values: dict[str, object] = {
            "statement_items": (("statement-001", Decimal("1000.00")),),
            "journal_total": Decimal("1000.00"),
            "reconciliation_run_reference": "run-001",
            "tenant_account_reference": "tenant-001",
        }
        values.update(overrides)
        return aggregate_allocations(**values)  # type: ignore[arg-type]

    def test_explicit_journal_identity_and_currency_remain_valid(self) -> None:
        """Explicit source bindings preserve exact aggregate allocation behavior."""
        allocations = self._plan(
            journal_reference="journal-001",
            currency_code="USD",
        )
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].journal_reference, "journal-001")
        self.assertEqual(allocations[0].currency_code, "USD")
        self.assertEqual(allocations[0].allocated_amount, Decimal("1000.00"))

    def test_omitted_journal_reference_fails_closed(self) -> None:
        """The planner cannot invent a journal identity for reviewable evidence."""
        with self.assertRaisesRegex(ValueError, "journal_reference"):
            self._plan(currency_code="KRW")

    def test_omitted_currency_fails_closed(self) -> None:
        """The planner cannot assume a currency for reviewable evidence."""
        with self.assertRaisesRegex(ValueError, "currency_code"):
            self._plan(journal_reference="journal-001")


if __name__ == "__main__":
    unittest.main()
