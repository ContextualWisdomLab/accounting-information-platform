"""Real PostgreSQL proof that lifecycle finalization uses one coherent source snapshot."""

from __future__ import annotations

import unittest
from datetime import timedelta
from threading import Event, Thread
from unittest import mock

import psycopg

from accounting_information_platform import (
    accept_reconciliation_run,
    reconcile_reconciliation_run,
)
from accounting_information_platform import reconciliation_lifecycle as lifecycle
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleSourceSnapshotPostgresTests(unittest.TestCase):
    """Prove source-population writes cannot create a mixed finalization snapshot."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating reconciliation run over retained statement evidence."""
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

    def _scope(self, connection: psycopg.Connection) -> tuple[object, object, object, str]:
        """Return tenant, statement, knowledge cutoff, and run currency."""
        row = connection.execute(
            """
            SELECT run.tenant_account_id,
                   command.bank_statement_record_id,
                   run.knowledge_cutoff_at,
                   run.currency_code
            FROM accounting_core.reconciliation_run AS run
            JOIN accounting_core.reconciliation_run_command AS command
              ON command.tenant_account_id = run.tenant_account_id
             AND command.reconciliation_run_id = run.reconciliation_run_id
            WHERE run.reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()
        assert row is not None
        return row[0], row[1], row[2], str(row[3])

    def _command(self) -> dict[str, object]:
        """Return one purpose-bound reconciliation lifecycle command."""
        return {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_idempotency_key": (
                "reconcile-coherent-source-snapshot-" + self.opened["reconciliation_run_id"]
            ),
            "actor_reference": "urn:cwl:principal:reconciliation_controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2026-09-02T00:30:00Z",
        }

    def test_late_source_insert_does_not_split_review_and_book_to_bank_snapshot(self) -> None:
        """A source insert after review reads must not enter the later bridge population."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, statement_id, knowledge_cutoff_at, currency_code = self._scope(
                connection
            )
            baseline = close_package._database_owned_close_projection_evidence(
                connection,
                tenant_id,
                reconciliation_run_reference=self.opened["reconciliation_run_id"],
            )

        review_state_read = Event()
        allow_bridge_read = Event()
        failures: list[BaseException] = []
        outcome: dict[str, object] = {}
        original_review_loader = lifecycle._load_review_control_state

        def gated_review_loader(
            connection: object, tenant_account_id: object, run_id: object
        ) -> object:
            state = original_review_loader(connection, tenant_account_id, run_id)
            review_state_read.set()
            if not allow_bridge_read.wait(timeout=10):
                raise TimeoutError("test did not release the lifecycle bridge read")
            return state

        def finalize() -> None:
            try:
                outcome.update(
                    reconcile_reconciliation_run(
                        self._command(),
                        posting.DATABASE_URL,
                        self.fixture.case.policy.tenant_reference,
                    )
                )
            except BaseException as error:  # pragma: no cover - surfaced below
                failures.append(error)

        with mock.patch.object(
            lifecycle,
            "_load_review_control_state",
            gated_review_loader,
        ):
            worker = Thread(target=finalize, name="lifecycle-source-snapshot")
            worker.start()
            self.assertTrue(review_state_read.wait(timeout=10))
            try:
                recorded_at = knowledge_cutoff_at - timedelta(microseconds=1)
                with psycopg.connect(posting.DATABASE_URL) as connection:
                    connection.execute(
                        """
                        INSERT INTO accounting_integration.bank_statement_entry (
                            tenant_account_id,
                            bank_statement_record_id,
                            source_entry_identity,
                            entry_sequence_number,
                            source_locator_path,
                            entry_amount,
                            entry_currency_code,
                            credit_debit_code,
                            source_entry_hash,
                            recorded_at
                        )
                        VALUES
                            (%s, %s, 'late-coherent-crdt', 900001,
                             '/late/coherent/1', 1.000000, %s, 'CRDT', %s, %s),
                            (%s, %s, 'late-coherent-dbit', 900002,
                             '/late/coherent/2', 1.000000, %s, 'DBIT', %s, %s)
                        """,
                        (
                            tenant_id,
                            statement_id,
                            currency_code,
                            "sha256:" + "8" * 64,
                            recorded_at,
                            tenant_id,
                            statement_id,
                            currency_code,
                            "sha256:" + "9" * 64,
                            recorded_at,
                        ),
                    )
                    connection.commit()
            finally:
                allow_bridge_read.set()
            worker.join(timeout=15)

        self.assertFalse(worker.is_alive())
        if failures:
            self.fail(f"reconciliation finalization failed: {failures!r}")
        self.assertEqual(outcome["run_status_code"], "reconciled")
        self.assertEqual(
            outcome["statement_population_reference"],
            baseline.statement_population_reference,
            "finalization mixed a later statement insert into an authority snapshot whose review state was already read",
        )


if __name__ == "__main__":
    unittest.main()
