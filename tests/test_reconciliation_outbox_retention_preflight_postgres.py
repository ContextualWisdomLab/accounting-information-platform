"""Real PostgreSQL upgrade acceptance for reconciliation outbox-retention preflight."""

from __future__ import annotations

import unittest
import uuid
from unittest import mock

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from accounting_information_platform import migration_install, resolve_reconciliation_exception
from tests import test_postgres_posting as posting
from tests.test_reconciliation_exception_resolution_postgres import (
    ReconciliationExceptionResolutionPostgresTests,
)


class ReconciliationOutboxRetentionPreflightPostgresTests(unittest.TestCase):
    """Prove migration 0023 cannot hide damaged authority behind FORCE RLS."""

    @classmethod
    def setUpClass(cls) -> None:
        """Ensure the shared cluster roles used by the foundation chain exist."""
        posting.PostgresPostingTests.setUpClass()

    @staticmethod
    def _database_url(
        database_name: str,
        *,
        role_name: str | None = None,
        password: str | None = None,
    ) -> str:
        """Return the CI DSN for one isolated upgrade database and optional login."""
        settings = conninfo_to_dict(posting.DATABASE_URL)
        settings["dbname"] = database_name
        if role_name is not None:
            settings["user"] = role_name
            settings["password"] = password or ""
        return make_conninfo(**settings)

    @staticmethod
    def _apply_pre_retention_chain(database_url: str) -> None:
        """Install through 0022 so migration 0023 can be exercised as an upgrade."""
        migration_root = posting.MIGRATION_PATH.parent
        migration_install._apply_foundation_migration(  # pylint: disable=protected-access
            database_url,
            posting.MIGRATION_PATH,
        )
        forward_names = (
            "0020_reconciliation_run_database_snapshot_authority.sql",
            "0021_reconciliation_exception_resolution_command.sql",
            "0022_reconciliation_exception_resolution_outbox_pair.sql",
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

    def test_non_bypass_upgrade_detects_damaged_command_event_pair(self) -> None:
        """A missing retained event is visible to a non-BYPASSRLS migration owner."""
        role_name = f"accounting_retention_upgrade_{uuid.uuid4().hex[:10]}"
        database_name = f"accounting_retention_upgrade_{uuid.uuid4().hex[:10]}"
        password = f"AisRetention{uuid.uuid4().hex}!"
        migration_url = self._database_url(
            database_name,
            role_name=role_name,
            password=password,
        )
        admin_url = self._database_url(database_name)
        migration_0023 = (
            posting.MIGRATION_PATH.parent
            / "0023_reconciliation_authority_outbox_retention.sql"
        ).read_text(encoding="utf-8")
        fixture: ReconciliationExceptionResolutionPostgresTests | None = None

        try:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOREPLICATION "
                        "NOBYPASSRLS CREATEROLE PASSWORD {}"
                    ).format(sql.Identifier(role_name), sql.Literal(password))
                )
                admin.execute(
                    sql.SQL(
                        "GRANT accounting_closing_writer TO {} WITH ADMIN OPTION"
                    ).format(sql.Identifier(role_name))
                )
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database_name),
                        sql.Identifier(role_name),
                    )
                )

            self._apply_pre_retention_chain(migration_url)

            with mock.patch.object(posting, "DATABASE_URL", migration_url):
                fixture = ReconciliationExceptionResolutionPostgresTests(
                    "test_named_command_resolves_exception_and_emits_atomic_outbox"
                )
                fixture.setUp()
                resolution = resolve_reconciliation_exception(
                    fixture._command(),
                    migration_url,
                    fixture.fixture.case.policy.tenant_reference,
                )

                with psycopg.connect(admin_url, autocommit=True) as admin_database:
                    authority = admin_database.execute(
                        """
                        SELECT tenant_account_id,
                               reconciliation_exception_id,
                               reconciliation_exception_resolution_command_id,
                               target_resolution_status_code,
                               reconciliation_exception_resolution_command_hash
                        FROM accounting_core.reconciliation_exception_resolution_command
                        WHERE reconciliation_exception_resolution_command_id = %s
                        """,
                        (resolution["reconciliation_exception_resolution_id"],),
                    ).fetchone()
                    assert authority is not None
                    event_type = (
                        "reconciliation_exception_resolved"
                        if authority[3] == "resolved"
                        else "reconciliation_exception_superseded"
                    )
                    aggregate_reference = (
                        "urn:cwl:accounting:reconciliation_exception:" + str(authority[1])
                    )
                    payload_reference = (
                        "urn:cwl:accounting:reconciliation_exception_resolution:"
                        + str(authority[2])
                    )
                    deleted = admin_database.execute(
                        """
                        DELETE FROM accounting_integration.outbox_event
                        WHERE tenant_account_id = %s
                          AND event_type_code = %s
                          AND aggregate_reference = %s
                          AND payload_reference = %s
                          AND payload_hash = %s
                        """,
                        (
                            authority[0],
                            event_type,
                            aggregate_reference,
                            payload_reference,
                            authority[4],
                        ),
                    ).rowcount
                    self.assertEqual(deleted, 1)

                with self.assertRaisesRegex(
                    psycopg.Error,
                    "reconciliation_authority_outbox_retention_preflight",
                ):
                    with psycopg.connect(
                        migration_url,
                        autocommit=True,
                        cursor_factory=psycopg.ClientCursor,
                    ) as migration_connection:
                        migration_connection.execute(migration_0023)

                with psycopg.connect(admin_url, autocommit=True) as admin_database:
                    failed_upgrade_policies = admin_database.execute(
                        """
                        SELECT policyname
                        FROM pg_catalog.pg_policies
                        WHERE policyname LIKE
                              'reconciliation_authority_retention_upgrade_%_visibility'
                        ORDER BY policyname
                        """
                    ).fetchall()
                    self.assertEqual(failed_upgrade_policies, [])
                    admin_database.execute(
                        """
                        INSERT INTO accounting_integration.outbox_event (
                            tenant_account_id,
                            event_type_code,
                            aggregate_reference,
                            payload_reference,
                            payload_hash
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            authority[0],
                            event_type,
                            aggregate_reference,
                            payload_reference,
                            authority[4],
                        ),
                    )

                with psycopg.connect(
                    migration_url,
                    autocommit=True,
                    cursor_factory=psycopg.ClientCursor,
                ) as migration_connection:
                    migration_connection.execute(migration_0023)
                    role_row = migration_connection.execute(
                        "SELECT rolbypassrls FROM pg_catalog.pg_roles "
                        "WHERE rolname = current_user"
                    ).fetchone()
                    remaining_policies = migration_connection.execute(
                        """
                        SELECT policyname
                        FROM pg_catalog.pg_policies
                        WHERE policyname LIKE
                              'reconciliation_authority_retention_upgrade_%_visibility'
                        ORDER BY policyname
                        """
                    ).fetchall()
                    durable_trigger = migration_connection.execute(
                        """
                        SELECT count(*)
                        FROM pg_catalog.pg_trigger
                        WHERE tgname = 'reconciliation_authority_outbox_retention_delete_guard'
                          AND NOT tgisinternal
                        """
                    ).fetchone()

                self.assertEqual(role_row, (False,))
                self.assertEqual(remaining_policies, [])
                self.assertEqual(durable_trigger, (1,))
        finally:
            if fixture is not None:
                fixture.doCleanups()
                fixture.tearDown()
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )


if __name__ == "__main__":
    unittest.main()
