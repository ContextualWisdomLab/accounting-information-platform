"""Real PostgreSQL acceptance for reconciliation lifecycle command authority."""

from __future__ import annotations

import threading
import time
import unittest
import unittest.mock as mock
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    reconcile_reconciliation_run,
    accept_reconciliation_run,
)
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.reconciliation_opening_book_fixture import post_reconciliation_opening_book_balance
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
        post_reconciliation_opening_book_balance(self.fixture.case)
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
                statement_population_reference,
                book_population_reference,
                reconciliation_transition_command_hash,
                actor_reference,
                purpose_code,
                effective_at
            )
            VALUES (%s, %s, %s, 'reconciled', %s, %s, %s, %s,
                    'urn:cwl:principal:test_controller',
                    'month_end_reconciliation', %s)
            """,
            (
                self._tenant_id(connection),
                self.opened["reconciliation_run_id"],
                f"direct-transition-{uuid.uuid4().hex}",
                "sha256:" + "d" * 64,
                "sha256:" + "1" * 64,
                "sha256:" + "2" * 64,
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
            connection.rollback()

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

    def test_review_evidence_cannot_move_to_another_run(self) -> None:
        """Existing evidence cannot escape its aggregate by rewriting run membership."""
        _statement, second_command = self.fixture._statement_and_command()
        second = accept_reconciliation_run(
            second_command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )
        exception_id = uuid.uuid4()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception (
                    reconciliation_exception_id,
                    tenant_account_id,
                    reconciliation_run_id,
                    exception_code,
                    owner_reference,
                    next_action,
                    effective_at,
                    resolution_status_code
                )
                VALUES (%s, %s, %s, 'scope_test',
                        'urn:cwl:principal:test_controller',
                        'Resolve this fixture exception.', %s, 'open')
                """,
                (
                    exception_id,
                    self._tenant_id(connection),
                    self.opened["reconciliation_run_id"],
                    datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc),
                ),
            )
            with self.assertRaisesRegex(psycopg.Error, "aggregate membership is immutable"):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_exception
                    SET reconciliation_run_id = %s
                    WHERE reconciliation_exception_id = %s
                    """,
                    (second["reconciliation_run_id"], exception_id),
                )
            connection.rollback()

    def test_waiting_lifecycle_observes_evidence_committed_before_lock_admission(self) -> None:
        """A blocked transition must open its authority snapshot after the writer commits."""
        run_id = str(self.opened["reconciliation_run_id"])
        tenant_reference = self.fixture.case.policy.tenant_reference
        command = self._command()
        bridge = _bridge(run_id)
        application_name = f"reconciliation_snapshot_wait_{uuid.uuid4().hex}"
        separator = "&" if "?" in posting.DATABASE_URL else "?"
        transition_database_url = (
            f"{posting.DATABASE_URL}{separator}application_name={application_name}"
        )
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def transition() -> None:
            try:
                with mock.patch.object(
                    close_package,
                    "_database_owned_close_projection_evidence",
                    return_value=bridge,
                ):
                    results.append(
                        reconcile_reconciliation_run(
                            command,
                            transition_database_url,
                            tenant_reference,
                        )
                    )
            except BaseException as error:  # noqa: BLE001 - test captures thread outcome
                errors.append(error)

        worker = threading.Thread(target=transition, name=application_name, daemon=True)
        blocked = False
        with psycopg.connect(posting.DATABASE_URL) as writer:
            tenant_id = self._tenant_id(writer)
            writer.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (tenant_reference, f"reconciliation_run_lifecycle:{run_id}"),
            )
            writer.execute(
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
                VALUES (%s, %s, 'concurrent_evidence',
                        'urn:cwl:principal:test_controller',
                        'Review the concurrently committed evidence.', %s, 'open')
                """,
                (
                    tenant_id,
                    run_id,
                    datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc),
                ),
            )
            worker.start()
            try:
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as observer:
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        wait_state = observer.execute(
                            """
                            SELECT wait_event_type, wait_event
                            FROM pg_stat_activity
                            WHERE application_name = %s
                              AND pid <> pg_backend_pid()
                            """,
                            (application_name,),
                        ).fetchone()
                        if wait_state is not None and wait_state[0] == "Lock":
                            blocked = True
                            break
                        time.sleep(0.01)
            finally:
                if blocked:
                    writer.commit()
                else:
                    writer.rollback()

        worker.join(timeout=5.0)
        self.assertTrue(blocked, "lifecycle command never reached the advisory-lock wait")
        self.assertFalse(worker.is_alive(), "lifecycle command remained blocked after writer commit")
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AccountingValidationError)
        self.assertIn("still open", str(errors[0]))
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            run_status_code = connection.execute(
                """
                SELECT run_status_code
                FROM accounting_core.reconciliation_run
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (tenant_id, run_id),
            ).fetchone()[0]
            transition_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.reconciliation_run_transition_command
                WHERE tenant_account_id = %s
                  AND reconciliation_run_id = %s
                """,
                (tenant_id, run_id),
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND aggregate_reference = %s
                  AND event_type_code = 'reconciliation_run_reconciled'
                """,
                (tenant_id, f"urn:cwl:accounting:reconciliation_run:{run_id}"),
            ).fetchone()[0]
        self.assertNotEqual(run_status_code, "reconciled")
        self.assertEqual(transition_count, 0)
        self.assertEqual(outbox_count, 0)

    def test_supported_command_persists_transition_outbox_and_freezes_review_state(self) -> None:
        """One exact command transitions atomically, replays provenance, and freezes evidence."""
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
        self.assertEqual(
            first["statement_population_reference"],
            replay["statement_population_reference"],
        )
        self.assertEqual(
            first["book_population_reference"],
            replay["book_population_reference"],
        )
        self.assertEqual(first["statement_population_reference"], bridge.statement_population_reference)
        self.assertEqual(first["book_population_reference"], bridge.book_population_reference)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            transition = connection.execute(
                """
                SELECT transition.reconciliation_transition_command_hash,
                       transition.reconciliation_snapshot_hash,
                       run.run_status_code,
                       transition.statement_population_reference,
                       transition.book_population_reference
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
            self.assertEqual(transition[3], bridge.statement_population_reference)
            self.assertEqual(transition[4], bridge.book_population_reference)
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
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
