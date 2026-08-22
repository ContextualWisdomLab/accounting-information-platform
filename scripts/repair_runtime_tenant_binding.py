"""One-shot normalizer for database-controlled runtime tenant binding."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    """Return one repository text file."""
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    """Replace one repository text file."""
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def _replace_once(path: str, old: str, new: str) -> None:
    """Replace exactly one expected source fragment."""
    text = _read(path)
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected exactly one repair anchor, found {text.count(old)}")
    _write(path, text.replace(old, new, 1))


def write_runtime_binding_migration() -> None:
    """Install an admin-owned login-to-tenant binding consumed by forced RLS."""
    _write(
        "database/migrations/0007_runtime_tenant_binding.sql",
        '''BEGIN;

CREATE TABLE accounting_core.runtime_tenant_binding (
    runtime_tenant_binding_id uuid PRIMARY KEY DEFAULT uuidv7(),
    runtime_role_oid oid NOT NULL,
    runtime_role_name name NOT NULL,
    tenant_account_id uuid NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT clock_timestamp(),
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id)
        REFERENCES accounting_core.tenant_account (tenant_account_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (runtime_tenant_binding_id, tenant_account_id)
);

CREATE UNIQUE INDEX runtime_tenant_binding_active_index
    ON accounting_core.runtime_tenant_binding (runtime_role_oid)
    WHERE valid_to IS NULL;

REVOKE ALL ON accounting_core.runtime_tenant_binding FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.current_tenant_account_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
    SELECT runtime_tenant_binding.tenant_account_id
    FROM accounting_core.runtime_tenant_binding
    JOIN pg_catalog.pg_roles
      ON pg_roles.oid = runtime_tenant_binding.runtime_role_oid
     AND pg_roles.rolname = runtime_tenant_binding.runtime_role_name
    WHERE runtime_tenant_binding.runtime_role_name = session_user
      AND runtime_tenant_binding.valid_from <= transaction_timestamp()
      AND (
            runtime_tenant_binding.valid_to IS NULL
            OR runtime_tenant_binding.valid_to > transaction_timestamp()
          )
    ORDER BY runtime_tenant_binding.valid_from DESC,
             runtime_tenant_binding.runtime_tenant_binding_id DESC
    LIMIT 1
$$;

COMMIT;''',
    )


def patch_migration_loader() -> None:
    """Require and apply the runtime-tenant migration after concurrency controls."""
    path = "src/accounting_information_platform/persistence.py"
    _replace_once(
        path,
        '''    concurrency_migration_path = migration_path.parent / "0006_concurrency_hot_partition.sql"
    if not concurrency_migration_path.is_file():
        raise AccountingValidationError(
            f"Concurrency and hot-partition migration is missing at {concurrency_migration_path}. "
            "Restore database/migrations/0006_concurrency_hot_partition.sql, then retry."
        )
    psycopg = _import_psycopg()
''',
        '''    concurrency_migration_path = migration_path.parent / "0006_concurrency_hot_partition.sql"
    if not concurrency_migration_path.is_file():
        raise AccountingValidationError(
            f"Concurrency and hot-partition migration is missing at {concurrency_migration_path}. "
            "Restore database/migrations/0006_concurrency_hot_partition.sql, then retry."
        )
    runtime_binding_migration_path = migration_path.parent / "0007_runtime_tenant_binding.sql"
    if not runtime_binding_migration_path.is_file():
        raise AccountingValidationError(
            f"Runtime-tenant binding migration is missing at {runtime_binding_migration_path}. "
            "Restore database/migrations/0007_runtime_tenant_binding.sql, then retry."
        )
    psycopg = _import_psycopg()
''',
    )
    _replace_once(
        path,
        '''            connection.execute(period_guard_migration_path.read_text(encoding="utf-8"))
            connection.execute(concurrency_migration_path.read_text(encoding="utf-8"))
''',
        '''            connection.execute(period_guard_migration_path.read_text(encoding="utf-8"))
            connection.execute(concurrency_migration_path.read_text(encoding="utf-8"))
            connection.execute(runtime_binding_migration_path.read_text(encoding="utf-8"))
''',
    )
    _replace_once(
        path,
        '''    def _require_tenant(self, connection: object) -> UUID:
        row = connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.tenant_account
            WHERE tenant_account_code = %s
            """,
            (self._tenant_reference,),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Tenant {self._tenant_reference} is not recorded. Create the tenant_account row, then retry posting."
            )
        connection.execute(
            "SELECT set_config('app.tenant_account_id', %s, true)",
            (str(row[0]),),
        )
        return row[0]
''',
        '''    def _require_tenant(self, connection: object) -> UUID:
        row = connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.tenant_account
            WHERE tenant_account_code = %s
            """,
            (self._tenant_reference,),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Tenant {self._tenant_reference} is not recorded. Create the tenant_account row, then retry posting."
            )
        requested_tenant_id = row[0]
        bound_tenant_id = connection.execute(
            "SELECT accounting_core.current_tenant_account_id()"
        ).fetchone()[0]
        if bound_tenant_id is not None:
            if bound_tenant_id != requested_tenant_id:
                raise AccountingValidationError(
                    "database runtime tenant binding does not match the requested tenant. "
                    "Use the database credential provisioned for this tenant, then retry."
                )
            return requested_tenant_id
        rolsuper, rolbypassrls = connection.execute(
            """
            SELECT rolsuper, rolbypassrls
            FROM pg_catalog.pg_roles
            WHERE rolname = session_user
            """
        ).fetchone()
        if rolsuper or rolbypassrls:
            return requested_tenant_id
        raise AccountingValidationError(
            "database runtime identity is not bound to a tenant. "
            "Provision accounting_core.runtime_tenant_binding for this login, then retry."
        )
''',
    )
    text = _read(path)
    text = text.replace(
        '"""Apply the checked-in PostgreSQL 18 foundation through concurrency indexes."""',
        '"""Apply the checked-in PostgreSQL 18 foundation through runtime tenant binding."""',
        1,
    )
    _write(path, text)


def patch_runtime_integration_test() -> None:
    """Provision the runtime binding and exercise match, mismatch, and unbound paths."""
    path = "tests/test_postgres_runtime_rls.py"
    text = _read(path)
    text = text.replace(
        "self._create_runtime_role(role_name, password)",
        "self._create_runtime_role(role_name, password, self.case.tenant_id)",
        1,
    )
    old_assert = '''        self.assertEqual(own_count, 1)
        self.assertEqual(other_count, 0)
        self.assertEqual(rebound_other_count, 0)

        with psycopg.connect(posting.DATABASE_URL) as admin:
'''
    new_assert = '''        self.assertEqual(own_count, 1)
        self.assertEqual(other_count, 0)
        self.assertEqual(rebound_other_count, 0)

        wrong_tenant_ledger = PostgresPostingLedger(runtime_url, other.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "does not match"):
            _ = wrong_tenant_ledger.journal_count

        unbound_role = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        unbound_password = f"AisRuntime{uuid.uuid4().hex}"
        self._create_runtime_role(unbound_role, unbound_password, None)
        self.addCleanup(self._drop_runtime_role, unbound_role)
        unbound_url = self._runtime_database_url(unbound_role, unbound_password)
        unbound_ledger = PostgresPostingLedger(unbound_url, self.case.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "not bound to a tenant"):
            _ = unbound_ledger.journal_count

        with psycopg.connect(posting.DATABASE_URL) as admin:
'''
    if old_assert not in text:
        raise SystemExit("tests/test_postgres_runtime_rls.py: assertion anchor drifted")
    text = text.replace(old_assert, new_assert, 1)
    text = text.replace(
        "from accounting_information_platform import PostgresPostingLedger",
        "from accounting_information_platform import AccountingValidationError, PostgresPostingLedger",
        1,
    )
    old_helper = '''    @staticmethod
    def _create_runtime_role(role_name: str, password: str) -> None:
        """Provision a test-only non-owner runtime with the current posting privileges."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role_name), sql.Literal(password))
            )
            admin.execute(
                sql.SQL(
                    "GRANT USAGE ON SCHEMA accounting_core, accounting_integration, accounting_reporting TO {}"
                ).format(sql.Identifier(role_name))
            )
            for schema_name in (
                "accounting_core",
                "accounting_integration",
                "accounting_reporting",
            ):
                admin.execute(
                    sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
            for schema_name, table_name in (
                ("accounting_integration", "journal_proposal_record"),
                ("accounting_core", "general_journal"),
                ("accounting_core", "journal_entry_line"),
                ("accounting_core", "journal_source_reference"),
                ("accounting_integration", "posting_receipt"),
                ("accounting_integration", "outbox_event"),
            ):
                admin.execute(
                    sql.SQL("GRANT INSERT ON {}.{} TO {}").format(
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                        sql.Identifier(role_name),
                    )
                )
'''
    new_helper = '''    @staticmethod
    def _create_runtime_role(role_name: str, password: str, tenant_id: object | None) -> None:
        """Provision a test-only runtime and optionally bind its authenticated login to a tenant."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role_name), sql.Literal(password))
            )
            admin.execute(
                sql.SQL(
                    "GRANT USAGE ON SCHEMA accounting_core, accounting_integration, accounting_reporting TO {}"
                ).format(sql.Identifier(role_name))
            )
            for schema_name in (
                "accounting_core",
                "accounting_integration",
                "accounting_reporting",
            ):
                admin.execute(
                    sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                        sql.Identifier(schema_name), sql.Identifier(role_name)
                    )
                )
            admin.execute(
                sql.SQL("REVOKE ALL ON accounting_core.runtime_tenant_binding FROM {}").format(
                    sql.Identifier(role_name)
                )
            )
            for schema_name, table_name in (
                ("accounting_integration", "journal_proposal_record"),
                ("accounting_core", "general_journal"),
                ("accounting_core", "journal_entry_line"),
                ("accounting_core", "journal_source_reference"),
                ("accounting_integration", "posting_receipt"),
                ("accounting_integration", "outbox_event"),
            ):
                admin.execute(
                    sql.SQL("GRANT INSERT ON {}.{} TO {}").format(
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                        sql.Identifier(role_name),
                    )
                )
            if tenant_id is not None:
                role_oid = admin.execute(
                    "SELECT oid FROM pg_catalog.pg_roles WHERE rolname = %s",
                    (role_name,),
                ).fetchone()[0]
                admin.execute(
                    """
                    INSERT INTO accounting_core.runtime_tenant_binding (
                        runtime_role_oid,
                        runtime_role_name,
                        tenant_account_id
                    ) VALUES (%s, %s, %s)
                    """,
                    (role_oid, role_name, tenant_id),
                )
'''
    if old_helper not in text:
        raise SystemExit("tests/test_postgres_runtime_rls.py: runtime helper anchor drifted")
    text = text.replace(old_helper, new_helper, 1)
    old_drop = '''        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role_name))
            )
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))
'''
    new_drop = '''        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                "DELETE FROM accounting_core.runtime_tenant_binding WHERE runtime_role_name = %s",
                (role_name,),
            )
            admin.execute(
                sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role_name))
            )
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))
'''
    if old_drop not in text:
        raise SystemExit("tests/test_postgres_runtime_rls.py: cleanup anchor drifted")
    _write(path, text.replace(old_drop, new_drop, 1))


def write_runtime_binding_contract_tests() -> None:
    """Add static and loader regressions for the runtime binding migration."""
    _write(
        "tests/test_runtime_tenant_binding_contract.py",
        '''"""Contracts for database-controlled runtime tenant identity."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.persistence import apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "database/migrations"
RUNTIME_BINDING_MIGRATION = MIGRATIONS / "0007_runtime_tenant_binding.sql"


class RuntimeTenantBindingContractTests(unittest.TestCase):
    """Keep tenant RLS authority anchored to the authenticated database login."""

    def test_runtime_binding_uses_session_user_and_not_caller_guc(self) -> None:
        """The RLS identity function resolves an admin-owned active binding for session_user."""
        migration = RUNTIME_BINDING_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE accounting_core.runtime_tenant_binding", migration)
        self.assertIn("runtime_role_oid oid NOT NULL", migration)
        self.assertIn("runtime_role_name name NOT NULL", migration)
        self.assertIn("tenant_account_id uuid NOT NULL", migration)
        self.assertIn("valid_from timestamptz NOT NULL", migration)
        self.assertIn("valid_to timestamptz", migration)
        self.assertIn("SECURITY DEFINER", migration)
        self.assertIn("SET search_path = pg_catalog, accounting_core", migration)
        self.assertIn("runtime_tenant_binding.runtime_role_name = session_user", migration)
        self.assertIn("pg_roles.oid = runtime_tenant_binding.runtime_role_oid", migration)
        self.assertNotIn("app.tenant_account_id", migration)

    def test_foundation_loader_requires_runtime_binding_migration(self) -> None:
        """A partial migration set fails before connecting instead of silently omitting tenant binding."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            for migration_number in range(1, 7):
                source = next(MIGRATIONS.glob(f"{migration_number:04d}_*.sql"))
                (temporary_root / source.name).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8"
                )
            with self.assertRaisesRegex(AccountingValidationError, "0007_runtime_tenant_binding"):
                apply_foundation_migration(
                    "postgresql://unused:unused@127.0.0.1:1/unused",
                    temporary_root / "0001_accounting_foundation.sql",
                )


if __name__ == "__main__":
    unittest.main()''',
    )


def write_runtime_binding_adr() -> None:
    """Record the database trust boundary and safe SECURITY DEFINER construction."""
    _write(
        "docs/adr/0049-runtime-tenant-database-binding.md",
        '''# ADR 0049: Bind runtime database logins to one tenant

**Status:** Accepted

## Context

Forced PostgreSQL row-level security is only a tenant boundary if the value used by each policy is itself trusted. A caller-writable custom GUC cannot be that authority: an ordinary application session can assign a custom two-part setting and, after credential compromise, could attempt to point the session at a different known tenant. PostgreSQL distinguishes `session_user`, the login that initiated the connection, from `current_user`, which can change under `SET ROLE` and while a `SECURITY DEFINER` function executes (PostgreSQL Global Development Group, 2026e). PostgreSQL also warns that security-definer functions must use a search path that excludes schemas writable by untrusted users (PostgreSQL Global Development Group, 2026f).

## Decision

`accounting_core.runtime_tenant_binding` is the database control-plane mapping from one authenticated runtime-role OID/name pair to one tenant. The mapping is admin-owned, effective-dated, and unavailable for direct runtime reads or writes. The active-role OID is retained alongside the role name so dropping and recreating a login with the same name does not silently inherit the former binding.

`accounting_core.current_tenant_account_id()` is a `STABLE SECURITY DEFINER` SQL function with `search_path = pg_catalog, accounting_core`. It takes no caller argument and derives the active tenant by joining the immutable session login (`session_user`) to the admin-owned binding and `pg_catalog.pg_roles`. Existing forced-RLS policies continue to call this function. The legacy `app.tenant_account_id` custom setting is no longer an authorization input; changing it cannot rebind a runtime session.

`PostgresPostingLedger` still receives an explicit tenant reference from the authenticated application boundary. It resolves that reference to the accounting tenant row and requires it to equal the database login binding. A bound mismatch or an unbound ordinary runtime identity fails closed before accounting work. Migration/superuser or `BYPASSRLS` identities are treated as administrative break-glass paths for migration/testing only and are not normal application credentials.

Provisioning or rotating an application DB login therefore requires an owner-controlled insert of its current PostgreSQL role OID, role name, and tenant into `runtime_tenant_binding`. Tenant reassignment closes the old binding and creates a new one; runtime credentials never mutate this table.

## Consequences

A compromised ordinary database credential cannot cross tenant scope merely by changing a request field, a session GUC, or `SET ROLE`. The database credential itself becomes purpose- and tenant-bound, complementing rather than replacing HTTP/OIDC authorization. Operators must provision the binding before switching traffic to a new runtime login, and backup/restore or role recreation must re-establish the current role OID deliberately. This is defense-in-depth evidence readiness, not a claim of SOC 2, CSAP, or jurisdictional certification.

## References

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: System information functions and operators*. https://www.postgresql.org/docs/18/functions-info.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: Function security*. https://www.postgresql.org/docs/18/perm-functions.html

PostgreSQL Global Development Group. (2026g). *PostgreSQL 18 documentation: CREATE POLICY*. https://www.postgresql.org/docs/18/sql-createpolicy.html''',
    )


def patch_docs() -> None:
    """Keep architecture, security, operations, data model, tests, and doctoring code-current."""
    reference_path = "docs/doctoring/REFERENCES.md"
    references = _read(reference_path)
    anchor = "PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Multicolumn indexes*. https://www.postgresql.org/docs/18/indexes-multicolumn.html\n"
    addition = anchor + '''
PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: System information functions and operators*. https://www.postgresql.org/docs/18/functions-info.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: Function security*. https://www.postgresql.org/docs/18/perm-functions.html

PostgreSQL Global Development Group. (2026g). *PostgreSQL 18 documentation: CREATE POLICY*. https://www.postgresql.org/docs/18/sql-createpolicy.html
'''
    if "PostgreSQL Global Development Group. (2026e)." not in references:
        if anchor not in references:
            raise SystemExit("REFERENCES.md PostgreSQL anchor drifted")
        references = references.replace(anchor, addition, 1)
        _write(reference_path, references)

    trace_path = "docs/doctoring/STANDARD_TRACEABILITY.md"
    trace = _read(trace_path)
    old_row = "| PostgreSQL 18.4 | Use current supported minor release, UUIDv7, exact numeric types, composite foreign keys, row-level security, transaction-level advisory locks, bounded lock waits, shared fiscal-period command locks, close row locks, tenant-leading high-write indexes, and a partition migration contract that preserves partition-key identity | Initial migration, concurrency/hot-partition migration, ADR 0050 |"
    new_row = "| PostgreSQL 18.4 | Use current supported minor release, UUIDv7, exact numeric types, composite foreign keys, forced row-level security, database-controlled `session_user` → tenant runtime binding, transaction-level advisory locks, bounded lock waits, shared fiscal-period command locks, close row locks, tenant-leading high-write indexes, and a partition migration contract that preserves partition-key identity. Ordinary runtime credentials cannot select or mutate the binding table and caller-controlled GUCs are not tenant authority | Initial migration, runtime-tenant binding migration, real restricted-runtime RLS tests, ADR 0049, ADR 0050 |"
    if old_row not in trace:
        raise SystemExit("STANDARD_TRACEABILITY PostgreSQL row drifted")
    _write(trace_path, trace.replace(old_row, new_row, 1))

    sections = {
        "docs/SECURITY.md": '''## Database tenant binding\n\nForced RLS derives the active tenant from the authenticated PostgreSQL `session_user` through admin-owned `accounting_core.runtime_tenant_binding` (ADR 0049), not from request payloads or a caller-writable custom GUC. Ordinary runtime credentials receive no direct privilege on that binding table. The application tenant reference must equal the database credential's active binding or the operation fails closed. Migration/superuser and `BYPASSRLS` identities remain separate administrative/break-glass paths and are not normal service credentials.\n''',
        "docs/OPERABILITY.md": '''## Runtime database tenant provisioning\n\nBefore routing accounting traffic to a new database login, an owner-controlled operator records that login's current PostgreSQL role OID, role name, and tenant in `accounting_core.runtime_tenant_binding`. The runtime login itself must have no direct privilege on the binding table. Recreating a role, restoring into a new cluster, or intentionally reassigning a tenant requires a fresh binding because the role OID is part of the identity. An unbound runtime or a requested tenant that disagrees with the binding fails closed; do not restore service by setting `app.tenant_account_id`.\n''',
        "docs/ARCHITECTURE.md": '''## Database tenant trust boundary\n\nThe HTTP/authentication adapter supplies a tenant reference, but PostgreSQL independently binds each ordinary runtime login to one tenant using `runtime_tenant_binding` and `session_user` (ADR 0049). Forced-RLS policies consume only that database-controlled identity. Request fields, model output, Billing proposals, and custom session GUCs cannot select another accounting tenant.\n''',
        "docs/DATA_MODEL.md": '''## Runtime tenant binding\n\n`accounting_core.runtime_tenant_binding` is a normalized control-plane relation from PostgreSQL runtime role OID/name to `tenant_account`. `valid_from`, `valid_to`, and `recorded_at` preserve assignment history; one partial unique index permits only one active binding per role OID. Runtime roles cannot directly read or mutate this relation. Its active row is resolved through the no-argument `current_tenant_account_id()` security-definer function.\n''',
        "docs/ERD.md": '''## Runtime tenant identity\n\n`tenant_account` is referenced by `runtime_tenant_binding`, which also records the authenticated PostgreSQL role OID/name and effective interval. This control-plane relation is not a business subledger; it supplies the trusted tenant key consumed by forced RLS for authoritative accounting tables.\n''',
        "docs/TEST_STRATEGY.md": '''## Runtime tenant credential isolation\n\nReal PostgreSQL tests create non-owner, non-superuser, non-`BYPASSRLS` runtime logins. A provisioned login must post and read its own tenant, must fail when the application requests a different tenant, and must remain unable to see another tenant even after rewriting the legacy `app.tenant_account_id` custom GUC. A separate unbound runtime login must fail closed. Static migration tests require the session-user/OID binding, locked search path, and migration-loader presence.\n''',
    }
    for path, section in sections.items():
        text = _read(path)
        heading = section.split("\n", 1)[0]
        if heading not in text:
            _write(path, text.rstrip() + "\n\n" + section)

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    bullet = "- Bound ordinary PostgreSQL runtime logins to one admin-owned tenant identity for forced RLS; caller-controlled custom GUCs can no longer rebind accounting tenant scope."
    if bullet not in changelog:
        marker = "## [Unreleased]\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG Unreleased anchor drifted")
        changelog = changelog.replace(marker, marker + "\n### Security\n\n" + bullet + "\n", 1)
        _write(changelog_path, changelog)


def main() -> None:
    """Apply the tested runtime-tenant binding repair and code-current documentation."""
    write_runtime_binding_migration()
    patch_migration_loader()
    patch_runtime_integration_test()
    write_runtime_binding_contract_tests()
    write_runtime_binding_adr()
    patch_docs()


if __name__ == "__main__":
    main()
