"""Real PostgreSQL regression for reconciliation aggregate membership immutability."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from datetime import datetime, timezone
import uuid

import psycopg

from accounting_information_platform import accept_reconciliation_run, reconcile_reconciliation_run
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.test_reconciliation_lifecycle_postgres import _bridge
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleAggregateMembershipPostgresTests(unittest.TestCase):
    """Keep reviewed evidence attached to the reconciliation run that owns it."""

    @classmethod
    def setUpClass(cls) -> None:
        """Provision the real PostgreSQL foundation used by accounting acceptance tests."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one ordinary evaluating run whose evidence will be finalized."""
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
        """Resolve the internal tenant identity for the opened run."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def _reconcile(self) -> None:
        """Finalize the opened run through the supported lifecycle command."""
        command = {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_idempotency_key": f"scope-freeze-{uuid.uuid4().hex}",
            "actor_reference": "urn:cwl:principal:test_controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2026-09-01T12:00:00Z",
        }
        with mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            return_value=_bridge(str(self.opened["reconciliation_run_id"])),
        ):
            reconcile_reconciliation_run(
                command,
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )

    def test_reconciled_exception_cannot_move_to_another_run(self) -> None:
        """A row cannot escape a reconciled aggregate by rewriting its run foreign key."""
        exception_id = None
        with psycopg.connect(posting.DATABASE_URL) as connection:
            exception_id = connection.execute(
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
                VALUES (%s, %s, 'reviewed_difference',
                        'urn:cwl:principal:test_controller',
                        'Retain the reviewed resolution evidence.', %s, 'resolved')
                RETURNING reconciliation_exception_id
                """,
                (
                    self._tenant_id(connection),
                    self.opened["reconciliation_run_id"],
                    datetime(2026, 9, 1, 11, 59, tzinfo=timezone.utc),
                ),
            ).fetchone()[0]

        self._reconcile()

        destination_run_id = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_run (
                    reconciliation_run_id,
                    tenant_account_id,
                    legal_entity_id,
                    accounting_book_id,
                    bank_account_assignment_id,
                    currency_code,
                    bank_cutoff_at,
                    book_cutoff_at,
                    matching_policy_version,
                    knowledge_cutoff_at,
                    run_status_code
                )
                SELECT %s,
                       tenant_account_id,
                       legal_entity_id,
                       accounting_book_id,
                       bank_account_assignment_id,
                       currency_code,
                       bank_cutoff_at,
                       book_cutoff_at,
                       matching_policy_version,
                       knowledge_cutoff_at,
                       'evaluating'
                FROM accounting_core.reconciliation_run
                WHERE reconciliation_run_id = %s
                """,
                (destination_run_id, self.opened["reconciliation_run_id"]),
            )
            try:
                with self.assertRaisesRegex(psycopg.Error, "aggregate membership is immutable"):
                    connection.execute(
                        """
                        UPDATE accounting_core.reconciliation_exception
                        SET reconciliation_run_id = %s
                        WHERE reconciliation_exception_id = %s
                        """,
                        (destination_run_id, exception_id),
                    )
            finally:
                connection.rollback()


if __name__ == "__main__":
    unittest.main()
