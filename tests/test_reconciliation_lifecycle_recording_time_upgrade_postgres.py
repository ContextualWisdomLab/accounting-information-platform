"""Real PostgreSQL upgrade acceptance for lifecycle recording-time authority."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_recording_time_upgrade_postgres import (
    ReconciliationRecordingTimeUpgradePostgresTests,
)
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleRecordingTimeUpgradePostgresTests(unittest.TestCase):
    """Prove migration 0025 never promotes unverifiable legacy transition time."""

    @classmethod
    def setUpClass(cls) -> None:
        """Ensure the shared PostgreSQL cluster roles exist."""
        posting.PostgresPostingTests.setUpClass()

    def test_non_bypass_upgrade_rejects_pre_0025_transition_chronology(self) -> None:
        """A caller-shaped historical transition blocks upgrade instead of gaining trust."""
        role_name, database_name, _password, migration_url, admin_url = (
            ReconciliationRecordingTimeUpgradePostgresTests._create_isolated_database()
        )
        migration_root = posting.MIGRATION_PATH.parent
        migration_0024 = (
            migration_root / "0024_reconciliation_control_recording_time_authority.sql"
        ).read_text(encoding="utf-8")
        migration_0025 = (
            migration_root / "0025_reconciliation_lifecycle_recording_time_authority.sql"
        ).read_text(encoding="utf-8")
        fixture: ReconciliationRunApiTests | None = None

        try:
            ReconciliationRecordingTimeUpgradePostgresTests._apply_pre_recording_time_chain(
                migration_url
            )
            with psycopg.connect(
                migration_url,
                autocommit=True,
                cursor_factory=psycopg.ClientCursor,
            ) as migration_connection:
                migration_connection.execute(migration_0024)

            with mock.patch.object(posting, "DATABASE_URL", migration_url):
                fixture = ReconciliationRunApiTests(
                    "test_open_run_binds_statement_scope_and_replays"
                )
                fixture.setUp()
                _statement, command = fixture._statement_and_command()
                opened = accept_reconciliation_run(
                    command,
                    migration_url,
                    fixture.case.policy.tenant_reference,
                )

            with psycopg.connect(admin_url, autocommit=True) as admin_database:
                tenant_id = admin_database.execute(
                    """
                    SELECT tenant_account_id
                    FROM accounting_core.reconciliation_run
                    WHERE reconciliation_run_id = %s
                    """,
                    (opened["reconciliation_run_id"],),
                ).fetchone()[0]
                admin_database.execute(
                    "ALTER TABLE accounting_core.reconciliation_run_transition_command "
                    "DISABLE TRIGGER USER"
                )
                try:
                    admin_database.execute(
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
                            effective_at,
                            recorded_at
                        )
                        VALUES (
                            %s, %s, 'legacy-transition-time-probe', 'reconciled',
                            %s, %s, %s, %s,
                            'urn:cwl:principal:legacy_controller',
                            'month_end_reconciliation', %s, %s
                        )
                        """,
                        (
                            tenant_id,
                            opened["reconciliation_run_id"],
                            "sha256:" + "1" * 64,
                            "sha256:" + "2" * 64,
                            "sha256:" + "3" * 64,
                            "sha256:" + "4" * 64,
                            datetime(2026, 9, 2, 0, 10, tzinfo=timezone.utc),
                            datetime(2100, 1, 1, tzinfo=timezone.utc),
                        ),
                    )
                finally:
                    admin_database.execute(
                        "ALTER TABLE accounting_core.reconciliation_run_transition_command "
                        "ENABLE TRIGGER USER"
                    )

            with psycopg.connect(
                migration_url,
                autocommit=True,
                cursor_factory=psycopg.ClientCursor,
            ) as migration_connection:
                with self.assertRaisesRegex(
                    psycopg.Error,
                    "reconciliation_lifecycle_legacy_recording_time_preflight",
                ):
                    migration_connection.execute(migration_0025)

            with psycopg.connect(admin_url, autocommit=True) as admin_database:
                authority_column = admin_database.execute(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'accounting_core'
                      AND table_name = 'reconciliation_run_transition_command'
                      AND column_name = 'recording_time_authority_code'
                    """
                ).fetchone()[0]
                temporary_policy = admin_database.execute(
                    """
                    SELECT count(*)
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'accounting_core'
                      AND tablename = 'reconciliation_run_transition_command'
                      AND policyname = 'reconciliation_lifecycle_recording_time_upgrade_visibility'
                    """
                ).fetchone()[0]
            self.assertEqual(authority_column, 0)
            self.assertEqual(temporary_policy, 0)
        finally:
            if fixture is not None:
                fixture.doCleanups()
                fixture.tearDown()
            ReconciliationRecordingTimeUpgradePostgresTests._drop_isolated_database(
                database_name, role_name
            )


if __name__ == "__main__":
    unittest.main()
