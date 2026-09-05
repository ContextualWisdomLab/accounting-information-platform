"""Real PostgreSQL regressions for book-period control and freshness-fence seeding."""

from __future__ import annotations

import unittest
import uuid
from datetime import date

import psycopg

from tests import test_postgres_posting as posting


class BookPeriodControlSeedTests(unittest.TestCase):
    """Require every active book-period pair to exist before journals can be admitted."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete current migration chain into the PostgreSQL fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated tenant whose period and book are created after migration install."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_seeded_book_and_existing_period_have_control_and_all_fences(self) -> None:
        """Creating an active book after its period must materialize close authority immediately."""
        control_count, fence_count = self._control_and_fence_counts("2026-08")

        self.assertEqual(control_count, 1)
        self.assertEqual(fence_count, 64)

    def test_period_open_seeds_control_and_all_fences_for_existing_book(self) -> None:
        """Opening a later period must materialize control and freshness rows for active books."""
        period_code = "2026-09"
        self.case.ledger.open_fiscal_period(
            self.case.policy.legal_entity_reference,
            period_code,
            date(2026, 9, 1),
            date(2026, 9, 30),
            idempotency_key=f"period-open:{uuid.uuid4()}",
            source_payload_hash="sha256:" + "7" * 64,
        )

        control_count, fence_count = self._control_and_fence_counts(period_code)

        self.assertEqual(control_count, 1)
        self.assertEqual(fence_count, 64)

    def _control_and_fence_counts(self, period_code: str) -> tuple[int, int]:
        """Return retained control and stripe cardinality for this fixture's primary book-period."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            book_id = connection.execute(
                """
                SELECT accounting_book_id
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s
                  AND book_name = %s
                  AND valid_to IS NULL
                """,
                (self.case.tenant_id, self.case.policy.accounting_book_reference),
            ).fetchone()[0]
            period_id = connection.execute(
                """
                SELECT fiscal_period_id
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND period_code = %s
                """,
                (self.case.tenant_id, period_code),
            ).fetchone()[0]
            control_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.accounting_book_period_control
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, book_id, period_id),
            ).fetchone()[0]
            fence_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.period_journal_population_fence
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                """,
                (self.case.tenant_id, book_id, period_id),
            ).fetchone()[0]
        return int(control_count), int(fence_count)


if __name__ == "__main__":
    unittest.main()
