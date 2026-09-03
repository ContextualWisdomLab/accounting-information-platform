"""Real PostgreSQL regression for stacked database-owned reconciliation authority."""

from __future__ import annotations

import unittest
from datetime import timedelta

import psycopg

from accounting_information_platform import accept_reconciliation_run
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

    def _begin_safe_raw_transition(self, connection: psycopg.Connection) -> None:
        """Enter the same pre-statement session/fresh-snapshot protocol as production."""
        lifecycle_scope = (
            "reconciliation_run_lifecycle:" + self.opened["reconciliation_run_id"]
        )
        connection.execute(
            "SELECT pg_advisory_lock(hashtext(%s), hashtext(%s))",
            (self.fixture.case.policy.tenant_reference, lifecycle_scope),
        )
        connection.commit()
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (self.fixture.case.policy.tenant_reference, lifecycle_scope),
        )

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
        """Insert raw transition evidence after entering the required lock protocol."""
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
                source_payload_hash,
                reconciliation_transition_command_hash,
                actor_reference,
                purpose_code,
                effective_at
            )
            VALUES (%s, %s, %s, 'reconciled', %s, %s, %s, %s, %s, %s, %s, %s)
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
                "sha256:" + "d" * 64,
                "sha256:" + "0" * 64,
                "urn:cwl:principal:database_authority_test",
                "month_end_reconciliation",
                "2026-09-02T00:30:00Z",
            ),
        ).fetchone()
        assert row is not None
        return row

    def _transition_identities_in_timezone(
        self, time_zone: str, key_suffix: str
    ) -> tuple[object, ...]:
        """Derive one rolled-back authority identity set under a caller session zone."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute("SELECT set_config('TimeZone', %s, false)", (time_zone,))
            self._begin_safe_raw_transition(connection)
            tenant_id, _statement_id, _knowledge_cutoff_at, _currency_code = self._scope(
                connection
            )
            persisted = self._insert_transition(
                connection,
                tenant_id=tenant_id,
                statement_population_reference="sha256:" + "a" * 64,
                book_population_reference="sha256:" + "b" * 64,
                snapshot_hash="sha256:" + "c" * 64,
                key_suffix=key_suffix,
            )
            connection.rollback()
        return persisted

    def test_database_replaces_all_caller_transition_identities(self) -> None:
        """Parent authority plus child evidence must replace all caller-selected identities."""
        forged_snapshot = "sha256:" + "f" * 64
        forged_statement_reference = "sha256:" + "a" * 64
        forged_book_reference = "sha256:" + "b" * 64
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._begin_safe_raw_transition(connection)
            tenant_id, _statement_id, _knowledge_cutoff_at, _currency_code = self._scope(
                connection
            )
            persisted = self._insert_transition(
                connection,
                tenant_id=tenant_id,
                statement_population_reference=forged_statement_reference,
                book_population_reference=forged_book_reference,
                snapshot_hash=forged_snapshot,
                key_suffix="forged-identities",
            )
            connection.rollback()

        self.assertNotEqual(persisted[0], forged_snapshot)
        self.assertNotEqual(persisted[1], forged_statement_reference)
        self.assertNotEqual(persisted[2], forged_book_reference)
        for value in persisted:
            with self.subTest(value=value):
                self.assertRegex(str(value), r"^sha256:[0-9a-f]{64}$")

    def test_database_authority_identities_ignore_caller_session_timezone(self) -> None:
        """Equivalent retained facts must hash identically in different session zones."""
        utc = self._transition_identities_in_timezone("UTC", "timezone-utc")
        seoul = self._transition_identities_in_timezone(
            "Asia/Seoul", "timezone-asia-seoul"
        )

        self.assertEqual(utc, seoul)

    def test_database_rejects_transition_when_authoritative_bridge_does_not_tie(self) -> None:
        """Direct SQL cannot reconcile a run after a statement source breaks the exact bridge."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._begin_safe_raw_transition(connection)
            tenant_id, statement_id, knowledge_cutoff_at, currency_code = self._scope(
                connection
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
                "reconciliation_database_statement_equation",
            ):
                self._insert_transition(
                    connection,
                    tenant_id=tenant_id,
                    statement_population_reference="sha256:" + "c" * 64,
                    book_population_reference="sha256:" + "d" * 64,
                    snapshot_hash="sha256:" + "e" * 64,
                    key_suffix="untied-bridge",
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
