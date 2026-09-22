"""RED contracts for repository-owned reconciliation Decimal evidence."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.reconciliation import BookJournalEvidence
from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation import StatementEntryEvidence


class _ExplodingFiniteDecimal(Decimal):
    def is_finite(self) -> bool:
        """Prove admission rejects the subclass before caller numeric logic runs."""
        raise RuntimeError("caller-controlled Decimal.is_finite must not execute")


class _ExplodingComparisonDecimal(Decimal):
    def __le__(self, other: object) -> bool:
        """Prove positive-money admission never delegates ordering to a subclass."""
        raise RuntimeError("caller-controlled Decimal comparison must not execute")


class ReconciliationExactDecimalRuntimeDomainRedTests(unittest.TestCase):
    """Keep reconciliation money inside the repository-owned Decimal domain."""

    @staticmethod
    def _statement(amount: object) -> StatementEntryEvidence:
        """Build otherwise-valid statement evidence while varying only amount."""
        return StatementEntryEvidence(
            statement_entry_reference="statement-decimal-runtime-1",
            provider_reference="provider-decimal-runtime-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=amount,  # type: ignore[arg-type]
            currency_code="KRW",
            credit_debit_code="CRDT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @staticmethod
    def _journal(amount: object) -> BookJournalEvidence:
        """Build otherwise-valid journal evidence while varying only amount."""
        return BookJournalEvidence(
            journal_reference="journal-decimal-runtime-1",
            provider_reference="provider-decimal-runtime-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=amount,  # type: ignore[arg-type]
            currency_code="KRW",
            credit_debit_code="CRDT",
            accounting_date=date(2026, 9, 22),
        )

    @staticmethod
    def _match_decision(amount: object) -> ReconciliationDecision:
        """Build an otherwise-valid match while varying only allocated amount."""
        return ReconciliationDecision(
            statement_entry_reference="statement-decimal-runtime-1",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-decimal-runtime-1",),
            allocated_amount=amount,  # type: ignore[arg-type]
            exception_code=None,
            next_action="Review the deterministic reconciliation proposal.",
        )

    @staticmethod
    def _abstain_decision(amount: object) -> ReconciliationDecision:
        """Build an otherwise-valid abstention while varying only allocated amount."""
        return ReconciliationDecision(
            statement_entry_reference="statement-decimal-runtime-1",
            decision_code="abstain",
            rule_code=None,
            matched_journal_references=(),
            allocated_amount=amount,  # type: ignore[arg-type]
            exception_code="no_candidate",
            next_action="Review the unmatched statement evidence.",
        )

    def test_source_amount_rejects_subclass_before_custom_numeric_methods_execute(self) -> None:
        """Statement and book evidence must reject Decimal subclasses at admission."""
        hostile_values = (
            _ExplodingFiniteDecimal("1000.00"),
            _ExplodingComparisonDecimal("1000.00"),
        )
        for factory in (self._statement, self._journal):
            for value in hostile_values:
                with self.subTest(factory=factory.__name__, value_type=type(value).__name__):
                    with self.assertRaisesRegex(
                        ValueError, "amount must be a positive exact Decimal"
                    ):
                        factory(value)

    def test_match_allocation_rejects_subclass_before_custom_numeric_methods_execute(self) -> None:
        """Match evidence must reject caller-defined Decimal behavior before validation."""
        for value in (
            _ExplodingFiniteDecimal("1000.00"),
            _ExplodingComparisonDecimal("1000.00"),
        ):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(
                    ValueError,
                    "match decision allocated_amount must be a positive exact Decimal",
                ):
                    self._match_decision(value)

    def test_abstention_zero_rejects_subclass_before_custom_numeric_methods_execute(self) -> None:
        """Zero-valued abstention evidence must also reject Decimal subclasses first."""
        with self.assertRaisesRegex(
            ValueError, "abstain decision allocated_amount must be exactly zero Decimal"
        ):
            self._abstain_decision(_ExplodingFiniteDecimal("0"))

    def test_exact_builtin_decimal_controls_remain_valid(self) -> None:
        """Exact built-in Decimal money preserves supported source and decision shapes."""
        statement = self._statement(Decimal("1000.00"))
        journal = self._journal(Decimal("1000.00"))
        match = self._match_decision(Decimal("1000.00"))
        abstain = self._abstain_decision(Decimal("0"))

        self.assertEqual(statement.amount, Decimal("1000.00"))
        self.assertEqual(journal.amount, Decimal("1000.00"))
        self.assertEqual(match.allocated_amount, Decimal("1000.00"))
        self.assertEqual(abstain.allocated_amount, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
