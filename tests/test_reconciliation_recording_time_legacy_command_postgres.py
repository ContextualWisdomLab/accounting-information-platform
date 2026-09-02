"""Real PostgreSQL command regression for legacy reconciliation recording-time evidence."""

from __future__ import annotations

import unittest
from unittest import mock

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    accept_reconciliation_run,
    resolve_reconciliation_exception,
)
from tests import test_postgres_posting as posting
from tests.test_reconciliation_recording_time_upgrade_postgres import (
    ReconciliationRecordingTimeUpgradePostgresTests,
)
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationRecordingTimeLegacyCommandPostgresTests(unittest.TestCase):
    """Keep legacy system-time rows auditable but outside new command authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Ensure the shared PostgreSQL roles required by isolated upgrade tests exist."""
        posting.PostgresPostingTests.setUpClass()

    def test_public_resolution_rejects_legacy_exception_and_review_time(self) -> None:
        """The public command must reject legacy chronology without leaking provider errors."""
        helper = ReconciliationRecordingTimeUpgradePostgresTests
        role_name, database_name, _password, migration_url, admin_url = (
            helper._create_isolated_database()
        )
        migration_0024 = (
            posting.MIGRATION_PATH.parent
            / "0024_reconciliation_control_recording_time_authority.sql"
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

            with psycopg.connect(admin_url, autocommit=True) as admin_database:
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
                        %s, %s, 'legacy_recording_time_command_probe',
                        'urn:cwl:principal:controller_owner',
                        'Preserve legacy evidence; create a new reviewed run after migration.',
                        '2026-09-02T00:10:00Z',
                        '2100-01-01T00:00:00Z',
                        'open'
                    )
                    RETURNING reconciliation_exception_id
                    """,
                    (tenant_id, opened["reconciliation_run_id"]),
                ).fetchone()[0]
                evidence_reference = (
                    f"urn:cwl:evidence:reconciliation_exception:{exception_id}:legacy-command"
                )
                evidence_hash = "sha256:" + "6" * 64
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

            with psycopg.connect(
                migration_url,
                autocommit=True,
                cursor_factory=psycopg.ClientCursor,
            ) as migration_connection:
                migration_connection.execute(migration_0024)

            resolution_command = {
                "tenant_reference": fixture.case.policy.tenant_reference,
                "reconciliation_action_code": "resolve_exception",
                "reconciliation_run_id": opened["reconciliation_run_id"],
                "reconciliation_exception_id": str(exception_id),
                "reconciliation_idempotency_key": f"legacy-time-{exception_id}",
                "resolution_status_code": "resolved",
                "actor_reference": "urn:cwl:principal:independent_reviewer",
                "purpose_code": "bank_reconciliation_exception_review",
                "resolution_evidence_reference": evidence_reference,
                "resolution_evidence_hash": evidence_hash,
                "effective_at": "2026-09-02T00:20:00Z",
            }
            with self.assertRaisesRegex(
                AccountingValidationError,
                "database-owned system-time",
            ):
                resolve_reconciliation_exception(
                    resolution_command,
                    migration_url,
                    fixture.case.policy.tenant_reference,
                )

            with psycopg.connect(admin_url, autocommit=True) as admin_database:
                authority_rows = admin_database.execute(
                    """
                    SELECT exception.recording_time_authority_code,
                           evidence.recording_time_authority_code,
                           exception.resolution_status_code
                    FROM accounting_core.reconciliation_exception AS exception
                    JOIN accounting_core.reconciliation_evidence AS evidence
                      ON evidence.tenant_account_id = exception.tenant_account_id
                     AND evidence.reconciliation_run_id = exception.reconciliation_run_id
                     AND evidence.reconciliation_exception_id = exception.reconciliation_exception_id
                    WHERE exception.reconciliation_exception_id = %s
                    """,
                    (exception_id,),
                ).fetchone()
                command_count = admin_database.execute(
                    """
                    SELECT count(*)
                    FROM accounting_core.reconciliation_exception_resolution_command
                    WHERE reconciliation_exception_id = %s
                    """,
                    (exception_id,),
                ).fetchone()[0]

            self.assertEqual(authority_rows, ("legacy_unverified", "legacy_unverified", "open"))
            self.assertEqual(command_count, 0)
        finally:
            if fixture is not None:
                fixture.doCleanups()
                fixture.tearDown()
            helper._drop_isolated_database(database_name, role_name)


if __name__ == "__main__":
    unittest.main()
