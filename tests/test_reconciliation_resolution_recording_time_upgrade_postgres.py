"""Real PostgreSQL upgrade acceptance for pre-0025 resolution authority."""

from __future__ import annotations

import unittest
from unittest import mock

import psycopg

from accounting_information_platform import accept_reconciliation_run, resolve_reconciliation_exception
from tests import test_postgres_posting as posting
from tests.test_reconciliation_recording_time_upgrade_postgres import (
    ReconciliationRecordingTimeUpgradePostgresTests,
)
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationResolutionRecordingTimeUpgradePostgresTests(unittest.TestCase):
    """Reject grandfathering commands backed by unverifiable source system time."""

    @classmethod
    def setUpClass(cls) -> None:
        """Ensure shared PostgreSQL roles required by isolated upgrade tests exist."""
        posting.PostgresPostingTests.setUpClass()

    def test_preexisting_resolution_command_blocks_recording_time_upgrade(self) -> None:
        """A pre-0025 authority command cannot inherit database-clock source provenance."""
        helper = ReconciliationRecordingTimeUpgradePostgresTests
        role_name, database_name, _password, migration_url, admin_url = (
            helper._create_isolated_database()
        )
        migration_0025 = (
            posting.MIGRATION_PATH.parent
            / "0025_reconciliation_control_recording_time_authority.sql"
        ).read_text(encoding="utf-8")
        fixture: ReconciliationRunApiTests | None = None

        try:
            helper._apply_pre_recording_time_chain(migration_url)
            with mock.patch.object(posting, "DATABASE_URL", migration_url):
                fixture = ReconciliationRunApiTests(
                    "test_open_run_binds_statement_scope_and_replays"
                )
                fixture.setUp()
                _statement, opening_command = fixture._statement_and_command()
                opened = accept_reconciliation_run(
                    opening_command,
                    migration_url,
                    fixture.case.policy.tenant_reference,
                )

            with psycopg.connect(admin_url) as admin_database:
                tenant_id = admin_database.execute(
                    """
                    SELECT tenant_account_id
                    FROM accounting_core.reconciliation_run
                    WHERE reconciliation_run_id = %s
                    """,
                    (opened["reconciliation_run_id"],),
                ).fetchone()[0]
                exception_id = admin_database.execute(
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
                        %s, %s, 'legacy_resolution_command_probe',
                        'urn:cwl:principal:controller_owner',
                        'Retain pre-upgrade review authority for provenance inspection.',
                        '2026-09-02T00:10:00Z',
                        '2100-01-01T00:00:00Z',
                        'open'
                    )
                    RETURNING reconciliation_exception_id
                    """,
                    (tenant_id, opened["reconciliation_run_id"]),
                ).fetchone()[0]
                evidence_reference = (
                    f"urn:cwl:evidence:reconciliation_exception:{exception_id}:legacy-resolution"
                )
                evidence_hash = "sha256:" + "9" * 64
                admin_database.execute(
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
                        %s, %s, %s, 'exception_resolution_review',
                        %s, %s, '2026-09-02T00:15:00Z',
                        '1900-01-01T00:00:00Z'
                    )
                    """,
                    (
                        tenant_id,
                        opened["reconciliation_run_id"],
                        exception_id,
                        evidence_reference,
                        evidence_hash,
                    ),
                )
                admin_database.commit()

            resolution_command = {
                "tenant_reference": fixture.case.policy.tenant_reference,
                "reconciliation_action_code": "resolve_exception",
                "reconciliation_run_id": opened["reconciliation_run_id"],
                "reconciliation_exception_id": str(exception_id),
                "reconciliation_idempotency_key": f"legacy-resolution-{exception_id}",
                "resolution_status_code": "resolved",
                "actor_reference": "urn:cwl:principal:independent_reviewer",
                "purpose_code": "bank_reconciliation_exception_review",
                "resolution_evidence_reference": evidence_reference,
                "resolution_evidence_hash": evidence_hash,
                "effective_at": "2026-09-02T00:20:00Z",
            }
            resolved = resolve_reconciliation_exception(
                resolution_command,
                migration_url,
                fixture.case.policy.tenant_reference,
            )
            self.assertEqual(resolved["resolution_status_code"], "resolved")

            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_resolution_legacy_recording_time_preflight",
            ):
                with psycopg.connect(
                    migration_url,
                    autocommit=True,
                    cursor_factory=psycopg.ClientCursor,
                ) as migration_connection:
                    migration_connection.execute(migration_0025)

            with psycopg.connect(admin_url, autocommit=True) as admin_database:
                command_count = admin_database.execute(
                    """
                    SELECT count(*)
                    FROM accounting_core.reconciliation_exception_resolution_command
                    WHERE reconciliation_exception_id = %s
                    """,
                    (exception_id,),
                ).fetchone()[0]
                authority_column_count = admin_database.execute(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'accounting_core'
                      AND table_name IN ('reconciliation_exception', 'reconciliation_evidence')
                      AND column_name = 'recording_time_authority_code'
                    """,
                ).fetchone()[0]
                visibility_policy_count = admin_database.execute(
                    """
                    SELECT count(*)
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'accounting_core'
                      AND tablename = 'reconciliation_exception_resolution_command'
                      AND policyname = 'reconciliation_resolution_recording_time_upgrade_visibility'
                    """,
                ).fetchone()[0]

            self.assertEqual(command_count, 1)
            self.assertEqual(authority_column_count, 0)
            self.assertEqual(visibility_policy_count, 0)
        finally:
            if fixture is not None:
                fixture.doCleanups()
                fixture.tearDown()
            helper._drop_isolated_database(database_name, role_name)


if __name__ == "__main__":
    unittest.main()
