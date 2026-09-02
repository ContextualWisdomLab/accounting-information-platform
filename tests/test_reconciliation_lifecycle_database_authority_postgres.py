"""Real PostgreSQL RED for database-owned reconciliation transition authority."""

from __future__ import annotations

import unittest
from datetime import timedelta

import psycopg

from accounting_information_platform import accept_reconciliation_run
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleDatabaseAuthorityPostgresTests(unittest.TestCase):
    """Reject caller-shaped lifecycle snapshots and database-untied bridge state."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating run over retained bank-statement evidence."""
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

    def _scope(
        self, connection: psycopg.Connection
    ) -> tuple[object, object, object, str]:
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

    def _insert_transition(
        self,
        connection: psycopg.Connection,
        *,
        tenant_id: object,
        statement_population_reference: str,
        book_population_reference: str,
        snapshot_hash: str,
        key_suffix: str,
    ) -> tuple[object, ...]:
        """Insert raw transition evidence without using the supported application command."""
        row = connection.execute(
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
            VALUES (%s, %s, %s, 'reconciled', %s, %s, %s, %s, %s, %s, %s)
            RETURNING reconciliation_snapshot_hash,
                      statement_population_reference,
                      book_population_reference
            """,
            (
                tenant_id,
                self.opened["reconciliation_run_id"],
                "database-authority-" + key_suffix,
                snapshot_hash,
                statement_population_reference,
                book_population_reference,
                "sha256:" + "0" * 64,
                "urn:cwl:principal:database_authority_test",
                "month_end_reconciliation",
                "2026-09-02T00:30:00Z",
            ),
        ).fetchone()
        assert row is not None
        return row

    def test_database_replaces_caller_snapshot_with_authoritative_digest(self) -> None:
        """A syntactically valid caller digest cannot become lifecycle snapshot authority."""
        forged = "sha256:" + "f" * 64
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, _statement_id, _knowledge_cutoff_at, _currency_code = self._scope(
                connection
            )
            baseline = close_package._database_owned_close_projection_evidence(
                connection,
                tenant_id,
                reconciliation_run_reference=self.opened["reconciliation_run_id"],
            )
            persisted = self._insert_transition(
                connection,
                tenant_id=tenant_id,
                statement_population_reference=baseline.statement_population_reference,
                book_population_reference=baseline.book_population_reference,
                snapshot_hash=forged,
                key_suffix="forged-snapshot",
            )
            connection.rollback()

        self.assertNotEqual(
            persisted[0],
            forged,
            "PostgreSQL accepted a caller-shaped reconciliation_snapshot_hash as authority",
        )
        self.assertRegex(str(persisted[0]), r"^sha256:[0-9a-f]{64}$")

    def test_database_rejects_transition_when_authoritative_bridge_does_not_tie(self) -> None:
        """Direct SQL cannot reconcile a run whose source-population bridge is untied."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, statement_id, knowledge_cutoff_at, currency_code = self._scope(
                connection
            )
            baseline = close_package._database_owned_close_projection_evidence(
                connection,
                tenant_id,
                reconciliation_run_reference=self.opened["reconciliation_run_id"],
            )
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
                VALUES (%s, %s, 'database-authority-untied', 910001,
                        '/database-authority/untied', 1.000000, %s, 'CRDT', %s, %s)
                """,
                (
                    tenant_id,
                    statement_id,
                    currency_code,
                    "sha256:" + "7" * 64,
                    knowledge_cutoff_at - timedelta(microseconds=1),
                ),
            )
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_lifecycle_bridge_mismatch",
            ):
                self._insert_transition(
                    connection,
                    tenant_id=tenant_id,
                    statement_population_reference=baseline.statement_population_reference,
                    book_population_reference=baseline.book_population_reference,
                    snapshot_hash="sha256:" + "e" * 64,
                    key_suffix="untied-bridge",
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
