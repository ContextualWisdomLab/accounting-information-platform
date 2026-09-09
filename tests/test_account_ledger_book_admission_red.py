"""RED contract for explicit accounting-book admission in General Ledger reads."""

from __future__ import annotations

import unittest

from accounting_information_platform.accept import lookup_account_ledger
from accounting_information_platform.core import AccountingValidationError


class AccountLedgerBookAdmissionRedTests(unittest.TestCase):
    """Keep the public library boundary fail-closed before any database lookup."""

    def test_library_rejects_empty_book_reference_before_database_lookup(self) -> None:
        """An empty explicit book identity must fail as validation, not reach persistence."""
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_account_ledger(
                database_url="postgresql://must-not-connect",
                tenant_reference="urn:cwl:tenant:book-admission-red",
                legal_entity_reference="KR-BOOK-ADMISSION-RED",
                book_reference="",
                chart_account_code="110100",
            )


if __name__ == "__main__":
    unittest.main()
