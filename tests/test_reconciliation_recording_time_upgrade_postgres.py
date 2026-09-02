"""Real PostgreSQL upgrade acceptance for reconciliation recording-time authority."""

from __future__ import annotations

import unittest
import uuid
from unittest import mock

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from accounting_information_platform import accept_reconciliation_run, migration_install
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationRecordingTimeUpgradePostgresTests(unittest.TestCase):
    """Prove migration 0024 does not relabel unverifiable legacy system time."""

    @classmethod
    def setUpClass(cls) -> None:
        """Ensure the shared cluster roles required by the foundation chain exist."""
        posting.PostgresPostingTests.setUpClass()

    @staticmethod
    def _database_url(
        database_name: str,
        *,
        role_name: str | None = None,
        password: str | None = None,
    ) -> str:
        """Return an isolated CI DSN for one database and optional login."""
        settings = conninfo_to_dict(posting.DATABASE_URL)
        settings["dbname"] = database_name
        if role_name is not None:
            settings["user"] = role_name
            settings["password"] = password or ""
        return make_conninfo(**settings)

    @staticmethod
    def _apply_pre_recording_time_chain(database_url: str) -> None:
        """Install the canonical reconciliation chain only through migration 0023."""
        migration_root = posting.MIGRATION_PATH.parent
        migration_install._apply_foundation_migration(  # pylint: disable=protected-access
            database_url,
            posting.MIGRATION_PATH,
        )
        forward_names = (
            "0019_reconciliation_run_database_snapshot_authority.sql",
            "0020_reconciliation_exception_resolution_command.sql",
            "0021_reconciliation_exception_resolution_outbox_pair.sql",
            "0022_reconciliation_authority_outbox_retention.sql",
            "0023_reconciliation_authority_outbox_orphan_guard.sql",
        )
        with psycopg.connect(
            database_url,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            for forward_name in forward_names:
                connection.execute(
                    (migration_root / forward_name).read_text(encoding="utf-8")
                )

    @staticmethod
    def _create_isolated_database() -> tuple[str, str, str, str, str]:
        """Create one non-BYPASSRLS migration owner and isolated database."""
        role_name = f"accounting_recording_upgrade_{uuid.uuid4().hex[:10]}"
        database_name = f"accounting_recording_upgrade_{uuid.uuid4().hex[:10]}"
        password = f"AisRecording{uuid.uuid4().hex}!"
        settings = conninfo_to_dict(posting.DATABASE_URL)
        settings["dbname"] = database_name
        admin_url = make_conninfo(**settings)
        settings["user"] = role_name
        settings["password"] = password
        migration_url = make_conninfo(**settings)

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOREPLICATION "
                    "NOBYPASSRLS CREATEROLE PASSWORD {}"
                ).format(sql.Identifier(role_name), sql.Literal(password))
            )
            admin.execute(
                sql.SQL("GRANT accounting_closing_writer TO {} WITH ADMIN OPTION").format(
                    sql.Identifier(role_name)
                )
            )
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(role_name),
                )
            )
        return role_name, database_name, password, migration_url, admin_url

    @staticmethod
    def _drop_isolated_database(database_name: str, role_name: str) -> None:
        """Remove the isolated database and migration login after an acceptance case."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))

    def test_non_bypass_upgrade_rejects_preexisting_unverifiable_control_rows(self) -> None:
        """Legacy exception/evidence rows remain untrusted instead of gaining new provenance."""
        role_name, database_name, _password, migration_url, admin_url = (
            self._create_isolated_database()
        )
        migration_0024 = (
            posting.MIGRATION_PATH.parent
            / "0024_reconciliation_control_recording_time_authority.sql"
        ).read_text(encoding="utf-8")
        fixture: ReconciliationRunApiTests | None = None

        try:
            self._apply_pre_recording_time_chain(migration_url)
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

                with psycopg.connect(migration_url) as connection:
                    tenant_id = connection.execute(
                        """
                        SELECT tenant_account_id
                        FROM accounting_core.reconciliation_run
                        WHERE reconciliation_run_id = %s
                        """,
                        (opened["reconciliation_run_id"],),
                    ).fetchone()[0]
                    exception_id, exception_recorded_at = connection.execute(
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
                            %s, %s, 'legacy_recording_time_probe',
                            'urn:cwl:principal:controller_owner',
                            'Retain for migration provenance review.',
                            '2026-09-02T00:10:00Z',
                            '2100-01-01T00:00:00Z',
                            'open'
                        )
                        RETURNING reconciliation_exception_id, recorded_at
                        """,
                        (tenant_id, opened["reconciliation_run_id"]),
                    ).fetchone()
                    evidence_recorded_at = connection.execute(
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
                        RETURNING recorded_at
                        """,
                        (
                            tenant_id,
                            opened["reconciliation_run_id"],
                            exception_id,
                            f"urn:cwl:evidence:reconciliation_exception:{exception_id}:legacy",
                            "sha256:" + "7" * 64,
                        ),
                    ).fetchone()[0]
                    connection.commit()

                self.assertEqual(str(exception_recorded_at.year), "2100")
                self.assertEqual(str(evidence_recorded_at.year), "1900")

            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_recording_time_legacy_preflight",
            ):
                with psycopg.connect(
                    migration_url,
                    autocommit=True,
                    cursor_factory=psycopg.ClientCursor,
                ) as migration_connection:
                    migration_connection.execute(migration_0024)

            with psycopg.connect(admin_url, autocommit=True) as admin_database:
                policies = admin_database.execute(
                    """
                    SELECT policyname
                    FROM pg_catalog.pg_policies
                    WHERE policyname IN (
                        'reconciliation_exception_recording_time_upgrade_visibility',
                        'reconciliation_evidence_recording_time_upgrade_visibility'
                    )
                    ORDER BY policyname
                    """
                ).fetchall()
            self.assertEqual(policies, [])
        finally:
            if fixture is not None:
                fixture.doCleanups()
                fixture.tearDown()
            self._drop_isolated_database(database_name, role_name)

    def test_empty_non_bypass_upgrade_installs_guards_and_removes_visibility(self) -> None:
        """A clean pre-0024 database installs durable guards without retaining broad policies."""
        role_name, database_name, _password, migration_url, admin_url = (
            self._create_isolated_database()
        )
        migration_0024 = (
            posting.MIGRATION_PATH.parent
            / "0024_reconciliation_control_recording_time_authority.sql"
        ).read_text(encoding="utf-8")

        try:
            self._apply_pre_recording_time_chain(migration_url)
            with psycopg.connect(
                migration_url,
                autocommit=True,
                cursor_factory=psycopg.ClientCursor,
            ) as migration_connection:
                migration_connection.execute(migration_0024)
                role_row = migration_connection.execute(
                    "SELECT rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user"
                ).fetchone()

            with psycopg.connect(admin_url, autocommit=True) as admin_database:
                policies = admin_database.execute(
                    """
                    SELECT policyname
                    FROM pg_catalog.pg_policies
                    WHERE policyname IN (
                        'reconciliation_exception_recording_time_upgrade_visibility',
                        'reconciliation_evidence_recording_time_upgrade_visibility'
                    )
                    ORDER BY policyname
                    """
                ).fetchall()
                triggers = admin_database.execute(
                    """
                    SELECT tgname
                    FROM pg_catalog.pg_trigger
                    WHERE tgname IN (
                        'reconciliation_exception_recording_time_guard',
                        'reconciliation_evidence_recording_time_guard'
                    )
                      AND NOT tgisinternal
                    ORDER BY tgname
                    """
                ).fetchall()

            self.assertEqual(role_row, (False,))
            self.assertEqual(policies, [])
            self.assertEqual(
                triggers,
                [
                    ("reconciliation_evidence_recording_time_guard",),
                    ("reconciliation_exception_recording_time_guard",),
                ],
            )
        finally:
            self._drop_isolated_database(database_name, role_name)


if __name__ == "__main__":
    unittest.main()
