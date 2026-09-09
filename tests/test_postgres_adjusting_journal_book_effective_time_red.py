"""Real PostgreSQL RED for accounting-effective book selection on adjusting journals."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    AccountingValidationError,
    accept_adjusting_journal,
)

from tests import test_postgres_posting as posting


class PostgresAdjustingJournalBookEffectiveTimeRedTests(unittest.TestCase):
    """Require an authoritative adjusting journal to bind the book effective on its journal date."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture for the write-path RED."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant, book, period, and chart for each run."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_adjusting_journal_rejects_book_not_effective_on_journal_date(self) -> None:
        """A book effective after the journal date must not receive an authoritative journal."""
        with self.case.ledger._session() as connection:
            tenant_id = self.case.ledger._require_tenant(connection)
            rows = connection.execute(
                """
                UPDATE accounting_core.accounting_book
                SET valid_from = TIMESTAMPTZ '2026-09-01 00:00:00+00'
                WHERE tenant_account_id = %s
                  AND book_name = %s
                  AND valid_to IS NULL
                RETURNING accounting_book_id
                """,
                (
                    tenant_id,
                    self.case.policy.accounting_book_reference,
                ),
            ).fetchall()
            self.assertEqual(len(rows), 1)

        journals_before = self.case._count_table("accounting_core.general_journal")
        payload = self.case._adjusting_journal_payload(
            journal_date="2026-08-31",
            idempotency_key=(
                f"{self.case.policy.tenant_reference}:adjusting-journal:book-effective-time:v1"
            ),
        )

        with self.assertRaises(AccountingValidationError):
            accept_adjusting_journal(
                payload,
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

        self.assertEqual(
            self.case._count_table("accounting_core.general_journal"),
            journals_before,
            "a not-yet-effective accounting book must not receive an adjusting journal",
        )


if __name__ == "__main__":
    unittest.main()
