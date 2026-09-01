"""Real PostgreSQL evidence for the reconciliation-completion migration boundary."""

from __future__ import annotations

import unittest
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from accounting_information_platform import apply_foundation_migration
from tests import test_postgres_posting as posting


class PostgresReconciliationCompletionMigrationTests(unittest.TestCase):
    """Install the full public chain and inspect its least-privilege completion controls."""

    def test_public_install_creates_forced_rls_completion_capability(self) -> None:
        """Migration 0020 is real PostgreSQL state, not a source-only contract."""
        role_name = f"accounting_completion_upgrade_{uuid.uuid4().hex[:10]}"
        database_name = f"accounting_completion_upgrade_{uuid.uuid4().hex[:10]}"
        password = f"AisCompletionUpgrade{uuid.uuid4().hex}!"
        migration_url = self._database_url(database_name, role_name, password)

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
                if admin.execute(
                    "SELECT to_regrole('accounting_reconciliation_completer')"
                ).fetchone()[0] is not None:
                    admin.execute(
                        sql.SQL(
                            "GRANT accounting_reconciliation_completer TO {} WITH ADMIN OPTION"
                        ).format(sql.Identifier(role_name))
                    )
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database_name), sql.Identifier(role_name)
                    )
                )

            apply_foundation_migration(migration_url, posting.MIGRATION_PATH)

            with psycopg.connect(migration_url) as connection:
                completion_table = connection.execute(
                    """
                    SELECT pg_class.relrowsecurity,
                           pg_class.relforcerowsecurity
                    FROM pg_class
                    JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                    WHERE pg_namespace.nspname = 'accounting_core'
                      AND pg_class.relname = 'reconciliation_completion_command'
                    """
                ).fetchone()
                capability_role = connection.execute(
                    """
                    SELECT rolcanlogin, rolsuper, rolbypassrls
                    FROM pg_catalog.pg_roles
                    WHERE rolname = 'accounting_reconciliation_completer'
                    """
                ).fetchone()
                trigger_names = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT trigger_name
                        FROM information_schema.triggers
                        WHERE event_object_schema = 'accounting_core'
                          AND event_object_table IN (
                              'reconciliation_completion_command',
                              'reconciliation_run'
                          )
                        """
                    ).fetchall()
                }
                run_guard_definition = connection.execute(
                    """
                    SELECT pg_get_functiondef(
                        'accounting_core.reconciliation_run_reconciled_guard()'::regprocedure
                    )
                    """
                ).fetchone()[0]
                completion_insert = connection.execute(
                    """
                    SELECT has_table_privilege(
                        'accounting_reconciliation_completer',
                        'accounting_core.reconciliation_completion_command',
                        'INSERT'
                    )
                    """
                ).fetchone()[0]
                run_update = connection.execute(
                    """
                    SELECT has_column_privilege(
                        'accounting_reconciliation_completer',
                        'accounting_core.reconciliation_run',
                        'run_status_code',
                        'UPDATE'
                    )
                    """
                ).fetchone()[0]
                outbox_insert = connection.execute(
                    """
                    SELECT has_table_privilege(
                        'accounting_reconciliation_completer',
                        'accounting_integration.outbox_event',
                        'INSERT'
                    )
                    """
                ).fetchone()[0]

            self.assertEqual(completion_table, (True, True))
            self.assertEqual(capability_role, (False, False, False))
            self.assertTrue(
                {
                    "reconciliation_completion_scope_guard",
                    "reconciliation_completion_command_immutability_guard",
                    "reconciliation_run_reconciled_guard",
                }
                <= trigger_names
            )
            self.assertIn("NEW.run_status_code <> 'reconciled'", run_guard_definition)
            self.assertIn("reconciliation_completion_target_forbidden", run_guard_definition)
            self.assertTrue(completion_insert)
            self.assertTrue(run_update)
            self.assertTrue(outbox_insert)
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )

    @staticmethod
    def _database_url(database_name: str, role_name: str, password: str) -> str:
        """Return the CI PostgreSQL DSN with a dedicated database and migration login."""
        settings = conninfo_to_dict(posting.DATABASE_URL)
        settings["dbname"] = database_name
        settings["user"] = role_name
        settings["password"] = password
        return make_conninfo(**settings)


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
