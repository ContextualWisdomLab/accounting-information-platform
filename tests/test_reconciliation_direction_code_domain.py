"""Domain contract for ISO 20022 credit/debit direction evidence."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    StatementEntryEvidence,
)


class ReconciliationDirectionCodeDomainTests(unittest.TestCase):
    """Reject direction values outside the normalized CRDT/DBIT evidence domain."""

    def test_statement_evidence_rejects_unknown_direction(self) -> None:
        """Invalid statement direction must fail before matching logic can inspect it."""
        with self.assertRaisesRegex(ValueError, "CRDT.*DBIT|DBIT.*CRDT"):
            StatementEntryEvidence(
                statement_entry_reference="statement-invalid-direction",
                provider_reference="provider-1",
                end_to_end_reference=None,
                account_servicer_reference=None,
                amount=Decimal("100.00"),
                currency_code="KRW",
                credit_debit_code="UNKNOWN",
                booking_date=date(2026, 8, 26),
                value_date=date(2026, 8, 26),
            )

    def test_book_evidence_rejects_unknown_direction(self) -> None:
        """Invalid book direction must fail before it can appear equal to invalid statement evidence."""
        with self.assertRaisesRegex(ValueError, "CRDT.*DBIT|DBIT.*CRDT"):
            BookJournalEvidence(
                journal_reference="journal-invalid-direction",
                provider_reference="provider-1",
                end_to_end_reference=None,
                account_servicer_reference=None,
                amount=Decimal("100.00"),
                currency_code="KRW",
                credit_debit_code="UNKNOWN",
                accounting_date=date(2026, 8, 26),
            )


if __name__ == "__main__":
    unittest.main()
