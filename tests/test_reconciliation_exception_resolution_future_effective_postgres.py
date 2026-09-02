"""Real PostgreSQL regression for future-effective exception-resolution authority."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    accept_reconciliation_run,
    resolve_reconciliation_exception,
)
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests

_EVIDENCE_HASH = "sha256:" + "f" * 64


class ReconciliationExceptionResolutionFutureEffectivePostgresTests(unittest.TestCase):
    """Reject terminal control state whose reviewed decision is not yet effective."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the shared PostgreSQL foundation through migration 0020."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one reconciliation run and persist one reviewable exception."""
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
        """Return the internal tenant identity for the opened run."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def _future_command(self) -> dict[str, object]:
        """Return a command whose business-effective review time is far in the future."""
        return {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "resolve_exception",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_exception_id": str(self.exception_id),
            "reconciliation_idempotency_key": f"future-resolve-{self.exception_id}",
            "resolution_status_code": "resolved",
            "actor_reference": "urn:cwl:principal:independent_reviewer",
            "purpose_code": "bank_reconciliation_exception_review",
            "resolution_evidence_reference": (
                f"urn:cwl:evidence:reconciliation_exception:{self.exception_id}:future-review"
            ),
            "resolution_evidence_hash": _EVIDENCE_HASH,
            "effective_at": "2099-01-01T00:00:00Z",
        }

    def test_future_effective_resolution_cannot_become_terminal_early(self) -> None:
        """A future valid-time decision leaves command, status, and outbox unchanged."""
        with self.assertRaisesRegex(
            (AccountingValidationError, psycopg.Error),
            "future|recording time|effective time",
        ):
            resolve_reconciliation_exception(
                self._future_command(),
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )

        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            status = connection.execute(
                """
                SELECT resolution_status_code
                FROM accounting_core.reconciliation_exception
                WHERE tenant_account_id = %s
                  AND reconciliation_exception_id = %s
                """,
                (tenant_id, self.exception_id),
            ).fetchone()[0]
            command_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.reconciliation_exception_resolution_command
                WHERE tenant_account_id = %s
                  AND reconciliation_exception_id = %s
                """,
                (tenant_id, self.exception_id),
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND aggregate_reference = %s
                """,
                (
                    tenant_id,
                    f"urn:cwl:accounting:reconciliation_exception:{self.exception_id}",
                ),
            ).fetchone()[0]

        self.assertEqual(status, "open")
        self.assertEqual(command_count, 0)
        self.assertEqual(outbox_count, 0)


if __name__ == "__main__":
    unittest.main()
