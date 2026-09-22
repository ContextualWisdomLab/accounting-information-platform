"""RED contract for repository-owned split statement evidence provenance.

Split allocation planning currently admits repository-owned posted-journal evidence
but receives the statement side as caller-assembled identity and amount scalars.
The planner must consume exact ``StatementEntryEvidence`` so reviewable split
evidence cannot detach money from statement currency, CRDT/DBIT direction, source
references, or dates. Statement evidence is admitted before attribute reads and
its executable allocation controls are revalidated at use.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest

from accounting_information_platform.allocation import propose_split_allocations
from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class _ExplodingStatementEvidence(StatementEntryEvidence):
    """Expose caller-defined attribute behavior if a statement subclass is admitted."""

    explode_amount_reads = False

    def __getattribute__(self, name: str):
        """Raise on amount reads only after canonical construction has completed."""
        if name == "amount" and type(self).explode_amount_reads:
            raise RuntimeError("caller-defined statement behavior executed")
        return super().__getattribute__(name)


class SplitStatementEvidenceProvenanceRedTests(unittest.TestCase):
    """Bind split statement money to exact repository-owned statement evidence."""

    @staticmethod
    def _statement(
        *,
        currency_code: str = "KRW",
        credit_debit_code: str = "DBIT",
        amount: str = "1000.00",
    ) -> StatementEntryEvidence:
        """Build one canonical bank-statement source while varying control fields."""
        return StatementEntryEvidence(
            statement_entry_reference="statement-001",
            provider_reference="provider-statement-001",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal(amount),
            currency_code=currency_code,
            credit_debit_code=credit_debit_code,
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @staticmethod
    def _journals(
        *,
        currency_code: str = "KRW",
        credit_debit_code: str = "DBIT",
    ) -> tuple[BookJournalEvidence, ...]:
        """Build two canonical posted-journal sources that conserve 1000 exactly."""
        return (
            BookJournalEvidence(
                journal_reference="journal-a",
                provider_reference="provider-journal-a",
                end_to_end_reference=None,
                account_servicer_reference=None,
                amount=Decimal("400.00"),
                currency_code=currency_code,
                credit_debit_code=credit_debit_code,
                accounting_date=date(2026, 9, 22),
            ),
            BookJournalEvidence(
                journal_reference="journal-b",
                provider_reference="provider-journal-b",
                end_to_end_reference=None,
                account_servicer_reference=None,
                amount=Decimal("600.00"),
                currency_code=currency_code,
                credit_debit_code=credit_debit_code,
                accounting_date=date(2026, 9, 22),
            ),
        )

    @staticmethod
    def _plan(statement_evidence: object):
        """Hold scope and journal population fixed while varying statement provenance."""
        return propose_split_allocations(
            statement_evidence=statement_evidence,  # type: ignore[arg-type]
            candidate_journals=SplitStatementEvidenceProvenanceRedTests._journals(),
            reconciliation_run_reference="run-001",
            tenant_account_reference="tenant-001",
        )

    def test_exact_statement_evidence_remains_valid(self) -> None:
        """Canonical statement evidence can produce an exactly conserved split."""
        allocations = self._plan(self._statement())
        self.assertEqual(len(allocations), 2)
        self.assertEqual(
            sum(allocation.allocated_amount for allocation in allocations),
            Decimal("1000.00"),
        )
        self.assertEqual(
            {allocation.statement_entry_reference for allocation in allocations},
            {"statement-001"},
        )
        self.assertEqual(
            {allocation.currency_code for allocation in allocations},
            {"KRW"},
        )

    def test_legacy_statement_scalars_fail_closed(self) -> None:
        """Identity and amount scalars cannot substitute for source statement evidence."""
        with self.assertRaisesRegex(ValueError, "StatementEntryEvidence"):
            propose_split_allocations(
                statement_entry_reference="statement-001",  # type: ignore[call-arg]
                statement_amount=Decimal("1000.00"),  # type: ignore[call-arg]
                candidate_journals=self._journals(),
                reconciliation_run_reference="run-001",
                tenant_account_reference="tenant-001",
            )

    def test_cross_currency_statement_fails_before_conservation(self) -> None:
        """Numerically equal money cannot reconcile KRW journals to a USD statement."""
        with self.assertRaisesRegex(ValueError, "currency"):
            self._plan(self._statement(currency_code="USD"))

    def test_opposite_direction_statement_fails_before_conservation(self) -> None:
        """A credit statement cannot become a debit-journal split because totals tie."""
        with self.assertRaisesRegex(ValueError, "direction"):
            self._plan(self._statement(credit_debit_code="CRDT"))

    def test_statement_subclass_fails_before_custom_attribute_behavior(self) -> None:
        """A subclass cannot execute caller-defined reads while becoming split evidence."""
        statement = _ExplodingStatementEvidence(
            statement_entry_reference="statement-001",
            provider_reference="provider-statement-001",
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
            self._plan(statement)


if __name__ == "__main__":
    unittest.main()
