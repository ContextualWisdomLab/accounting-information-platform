"""Real PostgreSQL regressions for database-owned reconciliation system time."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationExceptionRecordingTimePostgresTests(unittest.TestCase):
    """Require exception and retained review evidence to use database system time."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the shared PostgreSQL foundation through the current migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one reconciliation run whose control evidence can be persisted."""
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
        """Return the database tenant identity for the opened reconciliation run."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def _insert_exception(self, connection: psycopg.Connection, tenant_id: object) -> object:
        """Insert one open exception using ordinary database-owned recording time."""
        return connection.execute(
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
                %s,
                %s,
                'recording_time_forgery_probe',
                'urn:cwl:principal:controller_owner',
                'Review retained evidence before resolving this exception.',
                '2026-09-02T00:10:00Z',
                'open'
            )
            RETURNING reconciliation_exception_id
            """,
            (tenant_id, self.opened["reconciliation_run_id"]),
        ).fetchone()[0]

    def test_exception_recorded_at_is_database_owned_on_insert(self) -> None:
        """A privileged caller cannot forge future system time on maker evidence."""
        forged_recorded_at = datetime(2100, 1, 1, tzinfo=timezone.utc)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            before_insert = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            exception_id, recorded_at = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception (
                    tenant_account_id,
                    reconciliation_run_id,
                    exception_code,
                    owner_reference,
                    next_action,
                    effective_at,
                    recorded_at,
                    resolution_status_code
                )
                VALUES (
                    %s,
                    %s,
                    'exception_recorded_at_forgery',
                    'urn:cwl:principal:controller_owner',
                    'Review retained evidence before resolving this exception.',
                    '2026-09-02T00:10:00Z',
                    %s,
                    'open'
                )
                RETURNING reconciliation_exception_id, recorded_at
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    forged_recorded_at,
                ),
            ).fetchone()
            after_insert = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            persisted = connection.execute(
                """
                SELECT recorded_at
                FROM accounting_core.reconciliation_exception
                WHERE tenant_account_id = %s
                  AND reconciliation_exception_id = %s
                """,
                (tenant_id, exception_id),
            ).fetchone()[0]
            connection.rollback()

        self.assertNotEqual(recorded_at, forged_recorded_at)
        self.assertGreaterEqual(recorded_at, before_insert)
        self.assertLessEqual(recorded_at, after_insert)
        self.assertEqual(persisted, recorded_at)

    def test_retained_evidence_recorded_at_is_database_owned_on_insert(self) -> None:
        """A privileged caller cannot backdate or future-date retained review evidence."""
        forged_recorded_at = datetime(2100, 1, 1, tzinfo=timezone.utc)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            exception_id = self._insert_exception(connection, tenant_id)
            before_insert = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            evidence_id, recorded_at = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_evidence (
                    tenant_account_id,
                    reconciliation_run_id,
                    reconciliation_exception_id,
                    evidence_type_code,
                    evidence_reference,
                    evidence_payload_hash,
                    effective_at,
                    recorded_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    'exception_resolution_review',
                    %s,
                    %s,
                    '2026-09-02T00:15:00Z',
                    %s
                )
                RETURNING reconciliation_evidence_id, recorded_at
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    exception_id,
                    f"urn:cwl:evidence:reconciliation_exception:{exception_id}:recorded-at-probe",
                    "sha256:" + "9" * 64,
                    forged_recorded_at,
                ),
            ).fetchone()
            after_insert = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            persisted = connection.execute(
                """
                SELECT recorded_at
                FROM accounting_core.reconciliation_evidence
                WHERE tenant_account_id = %s
                  AND reconciliation_evidence_id = %s
                """,
                (tenant_id, evidence_id),
            ).fetchone()[0]
            connection.rollback()

        self.assertNotEqual(recorded_at, forged_recorded_at)
        self.assertGreaterEqual(recorded_at, before_insert)
        self.assertLessEqual(recorded_at, after_insert)
        self.assertEqual(persisted, recorded_at)


if __name__ == "__main__":
    unittest.main()
