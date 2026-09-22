"""RED contract for canonical aggregate statement-side evidence admission.

Aggregate allocation planning must not treat bare statement identity/amount pairs
as source evidence. Numeric equality alone cannot prove the statement money came
from an admitted source or even that it uses the journal currency. Exact
repository-owned ``StatementEntryEvidence`` must therefore own statement identity,
amount, and currency before aggregate conservation is evaluated.
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


class _ExplodingStatementEvidence(StatementEntryEvidence):
    """Prove subclass-defined source behavior cannot enter aggregate planning."""

    explode_amount_reads = False

    def __getattribute__(self, name: str):
        """Raise on amount reads only after canonical construction has completed."""
        if name == "amount" and type(self).explode_amount_reads:
            raise RuntimeError("caller-defined statement evidence behavior executed")
        return super().__getattribute__(name)


class AggregateStatementEvidenceDomainRedTests(unittest.TestCase):
    """Require exact ``StatementEntryEvidence`` for every aggregate statement source."""

    @staticmethod
    def _journal(*, currency_code: str = "KRW") -> BookJournalEvidence:
        """Build one canonical book-side source for aggregate planning."""
        return BookJournalEvidence(
            journal_reference="journal-001",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code=currency_code,
            credit_debit_code="DBIT",
            accounting_date=date(2026, 9, 22),
        )

    @staticmethod
    def _statement(*, currency_code: str = "KRW") -> StatementEntryEvidence:
        """Build one canonical statement source with the same exact money."""
        return StatementEntryEvidence(
            statement_entry_reference="statement-001",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code=currency_code,
            credit_debit_code="CRDT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @classmethod
    def _plan(cls, statement_item: object, *, journal_currency: str = "KRW"):
        """Plan one conserved aggregate while varying only statement provenance."""
        return aggregate_allocations(
            statement_items=(statement_item,),  # type: ignore[arg-type]
            journal_evidence=cls._journal(currency_code=journal_currency),
            reconciliation_run_reference="run-001",
            tenant_account_reference="tenant-001",
        )

    def test_exact_statement_evidence_remains_valid(self) -> None:
        """Repository-owned statement evidence remains a valid aggregate source."""
        allocations = self._plan(self._statement())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].statement_entry_reference, "statement-001")
        self.assertEqual(allocations[0].allocated_amount, Decimal("1000.00"))
        self.assertEqual(allocations[0].currency_code, "KRW")

    def test_raw_statement_identity_amount_pair_fails_closed(self) -> None:
        """A caller-assembled pair cannot substitute for admitted statement evidence."""
        with self.assertRaisesRegex(ValueError, "StatementEntryEvidence"):
            self._plan(("statement-001", Decimal("1000.00")))

    def test_statement_currency_must_match_journal_currency(self) -> None:
        """Equal numeric amounts in different currencies are not conserved money."""
        with self.assertRaisesRegex(ValueError, "currency"):
            self._plan(self._statement(currency_code="USD"), journal_currency="KRW")

    def test_statement_subclass_fails_before_custom_attribute_behavior(self) -> None:
        """A subclass fails before caller-defined amount reads can execute."""
        statement = _ExplodingStatementEvidence(
            statement_entry_reference="statement-subclass",
            provider_reference="provider-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="CRDT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )
        _ExplodingStatementEvidence.explode_amount_reads = True
        with self.assertRaisesRegex(ValueError, "StatementEntryEvidence"):
            self._plan(statement)


if __name__ == "__main__":
    unittest.main()
