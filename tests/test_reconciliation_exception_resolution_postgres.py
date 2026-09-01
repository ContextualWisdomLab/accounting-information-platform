"""Real PostgreSQL acceptance for maker-checker reconciliation exception resolution."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationExceptionResolutionPostgresTests(unittest.TestCase):
    """Prove mutable exception status cannot substitute for named command evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete accounting foundation in real PostgreSQL."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating run and persist one review exception."""
        self.fixture = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            self.exception_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception (
                    tenant_account_id,
                    reconciliation_run_id,
                    exception_code,
                    owner_reference,
                    next_action,
                    effective_at,
                    resolution_status_code
                )
                VALUES (
                    %s, %s, 'missing_book_candidate',
                    'urn:cwl:principal:controller_owner',
                    'Attach reviewed evidence and resolve through the named command.',
                    %s, 'open'
                )
                RETURNING reconciliation_exception_id
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    datetime(2026, 9, 2, 0, 10, tzinfo=timezone.utc),
                ),
            ).fetchone()[0]
            connection.commit()

    def _tenant_id(self, connection: psycopg.Connection) -> object:
        """Resolve the database tenant identity for the opened aggregate."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def test_raw_terminal_status_without_resolution_command_fails(self) -> None:
        """Privileged SQL cannot manufacture maker-checker resolution authority."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_exception_resolution_command_required",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_exception
                    SET resolution_status_code = 'resolved'
                    WHERE tenant_account_id = %s
                      AND reconciliation_exception_id = %s
                    """,
                    (self._tenant_id(connection), self.exception_id),
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
