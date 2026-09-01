"""Real PostgreSQL acceptance for reconciliation lifecycle command authority."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import psycopg

from accounting_information_platform import (
    reconcile_reconciliation_run,
    accept_reconciliation_run,
)
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


def _bridge(run_id: str) -> SimpleNamespace:
    """Return an exact bridge fixture; source-loader behavior has separate real tests."""
    return SimpleNamespace(
        reconciliation_run_reference=run_id,
        statement_population_reference="sha256:" + "1" * 64,
        book_population_reference="sha256:" + "2" * 64,
        currency_code="KRW",
        statement_opening_balance=Decimal("100000.00"),
        statement_period_movements=Decimal("15000.00"),
        statement_closing_balance=Decimal("115000.00"),
        book_opening_balance=Decimal("100000.00"),
        posted_cash_book_movements=Decimal("0.00"),
        book_closing_balance=Decimal("100000.00"),
        reconciled_book_balance=Decimal("100000.00"),
        outstanding_bank_items=Decimal("0.00"),
        outstanding_book_items=Decimal("15000.00"),
        unexplained_difference=Decimal("0.00"),
        status_code="reconciled",
    )


class ReconciliationLifecyclePostgresTests(unittest.TestCase):
    """Prove direct status SQL fails and the supported command persists authority."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = ReconciliationRunApiTests("test_open_run_binds_statement_scope_and_replays")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )

    def _command(self) -> dict[str, object]:
        """Return one purpose-bound lifecycle command for the opened run."""
        return {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_idempotency_key": f"reconcile-{uuid.uuid4().hex}",
            "actor_reference": "urn:cwl:principal:test_controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2026-09-01T12:00:00Z",
        }

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

    def _insert_transition_only(self, connection: psycopg.Connection) -> None:
        """Insert a syntactically valid command without its required paired status update."""
        connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run_transition_command (
                tenant_account_id,
                reconciliation_run_id,
                reconciliation_transition_idempotency_key,
                target_run_status_code,
                reconciliation_snapshot_hash,
                reconciliation_transition_command_hash,
                actor_reference,
                purpose_code,
                effective_at
            )
            VALUES (%s, %s, %s, 'reconciled', %s, %s,
                    'urn:cwl:principal:test_controller',
                    'month_end_reconciliation', %s)
            """,
            (
                self._tenant_id(connection),
                self.opened["reconciliation_run_id"],
                f"direct-transition-{uuid.uuid4().hex}",
                "sha256:" + "d" * 64,
                "sha256:" + "0" * 64,
                datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            ),
        )

    def test_direct_status_update_without_transition_command_fails(self) -> None:
        """Raw status SQL is not an owner-control path for reconciled authority."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(psycopg.Error, "lifecycle command"):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_run
                    SET run_status_code = 'reconciled'
                    WHERE reconciliation_run_id = %s
                    """,
                    (self.opened["reconciliation_run_id"],),
                )
                connection.commit()

    def test_transition_command_cannot_commit_without_reconciled_status(self) -> None:
        """A lifecycle command cannot be parked for a later raw status rewrite."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._insert_transition_only(connection)
            with self.assertRaisesRegex(psycopg.Error, "commit atomically"):
                connection.commit()
            connection.rollback()

    def test_pending_transition_command_freezes_review_evidence(self) -> None:
        """Evidence cannot change after a transition command snapshots the run."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._insert_transition_only(connection)
            with self.assertRaisesRegex(psycopg.Error, "evidence is frozen"):
                connection.execute(
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
                    VALUES (%s, %s, 'late_exception',
                            'urn:cwl:principal:test_controller',
                            'Create a new reconciliation run.', %s, 'open')
                    """,
                    (
                        self._tenant_id(connection),
                        self.opened["reconciliation_run_id"],
                        datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc),
                    ),
                )
            connection.rollback()

    def test_supported_command_persists_transition_outbox_and_freezes_review_state(self) -> None:
        """One exact command transitions atomically, replays, and freezes reviewed evidence."""
        command = self._command()
        bridge = _bridge(str(self.opened["reconciliation_run_id"]))
        with mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            return_value=bridge,
        ):
            first = reconcile_reconciliation_run(
                command,
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )
            replay = reconcile_reconciliation_run(
                command,
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )

        self.assertEqual(first["run_status_code"], "reconciled")
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            first["reconciliation_transition_command_hash"],
            replay["reconciliation_transition_command_hash"],
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            transition = connection.execute(
                """
                SELECT transition.reconciliation_transition_command_hash,
                       transition.reconciliation_snapshot_hash,
                       run.run_status_code
                FROM accounting_core.reconciliation_run_transition_command AS transition
                JOIN accounting_core.reconciliation_run AS run
                  ON run.tenant_account_id = transition.tenant_account_id
                 AND run.reconciliation_run_id = transition.reconciliation_run_id
                WHERE transition.reconciliation_run_id = %s
                """,
                (self.opened["reconciliation_run_id"],),
            ).fetchone()
            self.assertEqual(transition[0], first["reconciliation_transition_command_hash"])
            self.assertEqual(transition[1], first["reconciliation_snapshot_hash"])
            self.assertEqual(transition[2], "reconciled")
            outbox = connection.execute(
                """
                SELECT event_type_code, payload_hash
                FROM accounting_integration.outbox_event
                WHERE aggregate_reference = %s
                """,
                (
                    "urn:cwl:accounting:reconciliation_run:"
                    + str(self.opened["reconciliation_run_id"]),
                ),
            ).fetchone()
            self.assertEqual(outbox[0], "reconciliation_run_reconciled")
            self.assertEqual(outbox[1], first["reconciliation_transition_command_hash"])
            with self.assertRaisesRegex(psycopg.Error, "evidence is frozen"):
                connection.execute(
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
                    VALUES (%s, %s, 'late_exception', 'urn:cwl:principal:test_controller',
                            'Create a new reconciliation run.', %s, 'open')
                    """,
                    (
                        self._tenant_id(connection),
                        self.opened["reconciliation_run_id"],
                        datetime(2026, 9, 1, 12, 2, tzinfo=timezone.utc),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
