"""Real PostgreSQL regression for future-effective reconciliation lifecycle authority."""

from __future__ import annotations

import unittest
import unittest.mock as mock
import uuid

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    accept_reconciliation_run,
    reconcile_reconciliation_run,
)
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.test_reconciliation_lifecycle_postgres import _bridge
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleFutureEffectivePostgresTests(unittest.TestCase):
    """Reject reconciled authority whose business-valid time has not arrived."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL reconciliation authority chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one otherwise-finalizable reconciliation run."""
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

    def _tenant_id(self, connection: psycopg.Connection) -> object:
        """Return the database tenant identity for the opened run."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def _future_command(self) -> dict[str, object]:
        """Return a lifecycle decision whose valid time is far in the future."""
        return {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_idempotency_key": f"future-reconcile-{uuid.uuid4().hex}",
            "actor_reference": "urn:cwl:principal:test_controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2099-01-01T00:00:00Z",
        }

    def _assert_no_lifecycle_side_effects(self) -> None:
        """Require the run, transition command, and authority event to remain unchanged."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            run_status = connection.execute(
                """
                SELECT run_status_code
                FROM accounting_core.reconciliation_run
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (tenant_id, self.opened["reconciliation_run_id"]),
            ).fetchone()[0]
            transition_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_core.reconciliation_run_transition_command
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (tenant_id, self.opened["reconciliation_run_id"]),
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND aggregate_reference = %s
                  AND event_type_code = 'reconciliation_run_reconciled'
                """,
                (
                    tenant_id,
                    "urn:cwl:accounting:reconciliation_run:"
                    + str(self.opened["reconciliation_run_id"]),
                ),
            ).fetchone()[0]

        self.assertEqual(run_status, "evaluating")
        self.assertEqual(transition_count, 0)
        self.assertEqual(outbox_count, 0)

    def test_future_effective_reconciliation_cannot_become_authoritative_early(self) -> None:
        """A future lifecycle decision cannot create current reconciled authority."""
        bridge = _bridge(str(self.opened["reconciliation_run_id"]))
        with mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            return_value=bridge,
        ):
            with self.assertRaisesRegex(
                (AccountingValidationError, psycopg.Error),
                "future|effective time|recording time",
            ):
                reconcile_reconciliation_run(
                    self._future_command(),
                    posting.DATABASE_URL,
                    self.fixture.case.policy.tenant_reference,
                )

        self._assert_no_lifecycle_side_effects()


if __name__ == "__main__":
    unittest.main()
