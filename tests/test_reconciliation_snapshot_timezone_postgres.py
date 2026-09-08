"""Real PostgreSQL determinism checks for reconciliation snapshot authority."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.reconciliation_opening_book_fixture import post_reconciliation_opening_book_balance
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationSnapshotTimezonePostgresTests(unittest.TestCase):
    """Prove database-owned snapshot digests are independent of session TimeZone."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        post_reconciliation_opening_book_balance(self.fixture.case)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )

    def _authority_digest(self, timezone_name: str) -> tuple[str, str, str]:
        """Return one server-derived snapshot under the requested database session zone."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute("SELECT set_config('TimeZone', %s, false)", (timezone_name,))
            tenant_id = connection.execute(
                """
                SELECT tenant_account_id
                FROM accounting_core.reconciliation_run
                WHERE reconciliation_run_id = %s
                """,
                (self.opened["reconciliation_run_id"],),
            ).fetchone()[0]
            row = connection.execute(
                """
                SELECT database_snapshot_hash,
                       database_statement_reference,
                       database_book_reference
                FROM accounting_core.reconciliation_run_database_snapshot_authority(%s, %s)
                """,
                (tenant_id, self.opened["reconciliation_run_id"]),
            ).fetchone()
        return str(row[0]), str(row[1]), str(row[2])

    def test_same_retained_facts_hash_identically_in_utc_and_asia_seoul(self) -> None:
        """Session TimeZone cannot change any authority-bearing population or snapshot digest."""
        utc = self._authority_digest("UTC")
        seoul = self._authority_digest("Asia/Seoul")

        self.assertEqual(utc, seoul)
        self.assertTrue(all(value.startswith("sha256:") for value in utc))


if __name__ == "__main__":
    unittest.main()
