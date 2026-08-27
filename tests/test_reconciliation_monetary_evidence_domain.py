"""Money-domain contracts for deterministic bank reconciliation evidence."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform import reconciliation


class ReconciliationMonetaryEvidenceDomainTests(unittest.TestCase):
    """Reject non-canonical or non-positive money before candidate matching."""

    def _statement(self, amount: object) -> reconciliation.StatementEntryEvidence:
        return reconciliation.StatementEntryEvidence(
            statement_entry_reference="stmt-entry-money-domain",
            provider_reference="provider-money-domain",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=amount,  # type: ignore[arg-type]
            currency_code="KRW",
            credit_debit_code="CRDT",
            booking_date=date(2026, 8, 27),
            value_date=date(2026, 8, 27),
        )

    def _journal(self, amount: object) -> reconciliation.BookJournalEvidence:
        return reconciliation.BookJournalEvidence(
            journal_reference="journal-money-domain",
            provider_reference="provider-money-domain",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=amount,  # type: ignore[arg-type]
            currency_code="KRW",
            credit_debit_code="CRDT",
            accounting_date=date(2026, 8, 27),
        )

    def test_zero_amount_statement_evidence_is_rejected_before_matching(self) -> None:
        """A zero-value bank entry must never become a success-shaped match."""
        with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
            self._statement(Decimal("0"))

    def test_negative_book_amount_is_rejected_before_matching(self) -> None:
        """Direction is carried separately, so a signed book amount is invalid evidence."""
        with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
            self._journal(Decimal("-1.00"))

    def test_non_finite_amounts_are_rejected_before_matching(self) -> None:
        """NaN and infinities cannot participate in exact accounting comparisons."""
        for amount in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with self.subTest(amount=str(amount)):
                with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
                    self._statement(amount)

    def test_binary_float_amount_is_rejected_before_matching(self) -> None:
        """Binary floats cannot enter an exact-decimal reconciliation decision."""
        with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
            self._journal(25000.0)


if __name__ == "__main__":
    unittest.main()
