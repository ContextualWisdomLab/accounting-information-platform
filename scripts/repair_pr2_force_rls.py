"""One-shot repair that makes tenant RLS mandatory for table-owner runtime roles."""

from __future__ import annotations

from pathlib import Path


_TENANT_TABLES = (
    ("accounting_core", "legal_entity_record"),
    ("accounting_core", "accounting_book"),
    ("accounting_core", "chart_account"),
    ("accounting_core", "account_role_mapping"),
    ("accounting_core", "fiscal_calendar"),
    ("accounting_core", "fiscal_period"),
    ("accounting_integration", "journal_proposal_record"),
    ("accounting_core", "general_journal"),
    ("accounting_core", "journal_entry_line"),
    ("accounting_core", "journal_source_reference"),
    ("accounting_core", "journal_reversal"),
    ("accounting_integration", "posting_receipt"),
    ("accounting_reporting", "trial_balance_snapshot"),
    ("accounting_reporting", "trial_balance_line"),
    ("accounting_integration", "outbox_event"),
    ("accounting_integration", "home_tax_submission"),
)


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def add_force_rls_migration() -> None:
    """Add a migration forcing RLS even when a runtime role owns a table."""
    path = Path("database/migrations/0006_force_tenant_rls.sql")
    if path.exists():
        return
    statements = ["BEGIN;", ""]
    for schema_name, table_name in _TENANT_TABLES:
        statements.append(
            f"ALTER TABLE {schema_name}.{table_name} FORCE ROW LEVEL SECURITY;"
        )
    statements.extend(["", "COMMIT;", ""])
    path.write_text("\n".join(statements), encoding="utf-8")


def apply_force_rls_migration() -> None:
    """Extend the foundation installer so the forced-RLS migration is never skipped."""
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)
    old_doc = (
        '    """Apply the checked-in PostgreSQL 18 foundation through the closed-period guard."""\n'
    )
    new_doc = (
        '    """Apply every checked-in PostgreSQL 18 accounting foundation migration."""\n'
    )
    if old_doc not in text:
        raise SystemExit("foundation migration docstring anchor drifted")
    text = text.replace(old_doc, new_doc, 1)

    guard_check = '''    period_guard_migration_path = migration_path.parent / "0005_closed_period_guard.sql"
    if not period_guard_migration_path.is_file():
        raise AccountingValidationError(
            f"Closed-period guard migration is missing at {period_guard_migration_path}. "
            "Restore database/migrations/0005_closed_period_guard.sql, then retry."
        )
    psycopg = _import_psycopg()
'''
    force_check = '''    period_guard_migration_path = migration_path.parent / "0005_closed_period_guard.sql"
    if not period_guard_migration_path.is_file():
        raise AccountingValidationError(
            f"Closed-period guard migration is missing at {period_guard_migration_path}. "
            "Restore database/migrations/0005_closed_period_guard.sql, then retry."
        )
    force_rls_migration_path = migration_path.parent / "0006_force_tenant_rls.sql"
    if not force_rls_migration_path.is_file():
        raise AccountingValidationError(
            f"Forced-RLS migration is missing at {force_rls_migration_path}. "
            "Restore database/migrations/0006_force_tenant_rls.sql, then retry."
        )
    psycopg = _import_psycopg()
'''
    if guard_check not in text:
        raise SystemExit("foundation forced-RLS path anchor drifted")
    text = text.replace(guard_check, force_check, 1)

    execute_anchor = '''            connection.execute(close_key_migration_path.read_text(encoding="utf-8"))
            connection.execute(period_guard_migration_path.read_text(encoding="utf-8"))
'''
    execute_replacement = execute_anchor + '''            connection.execute(force_rls_migration_path.read_text(encoding="utf-8"))
'''
    if execute_anchor not in text:
        raise SystemExit("foundation forced-RLS execution anchor drifted")
    _write(path, text.replace(execute_anchor, execute_replacement, 1))


def add_force_rls_regression() -> None:
    """Prove every tenant-scoped authoritative table has enabled and forced RLS."""
    path = "tests/test_postgres_posting.py"
    tests = _read(path)
    if "test_database_forces_rls_on_tenant_scoped_tables" in tests:
        return
    marker = "    def _seed_master_data(self, *, period_status_code: str) -> str:\n"
    expected_repr = repr(_TENANT_TABLES)
    regression = f'''    def test_database_forces_rls_on_tenant_scoped_tables(self) -> None:
        """Tenant policies also bind a table-owner runtime role."""
        expected_tables = {expected_repr}
        with psycopg.connect(DATABASE_URL) as connection:
            for schema_name, table_name in expected_tables:
                row = connection.execute(
                    """
                    SELECT relation.relrowsecurity, relation.relforcerowsecurity
                    FROM pg_catalog.pg_class AS relation
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = %s
                      AND relation.relname = %s
                    """,
                    (schema_name, table_name),
                ).fetchone()
                self.assertIsNotNone(row, (schema_name, table_name))
                self.assertEqual(row, (True, True), (schema_name, table_name))

'''
    if marker not in tests:
        raise SystemExit("PostgreSQL FORCE RLS test insertion marker drifted")
    _write(path, tests.replace(marker, regression + marker, 1))


def update_security_docs() -> None:
    """Document the non-superuser runtime role contract and forced RLS."""
    security_path = "docs/SECURITY.md"
    security = _read(security_path)
    old = "- Tenant-scoped composite foreign keys and PostgreSQL row-level security.\n"
    new = (
        "- Tenant-scoped composite foreign keys plus PostgreSQL row-level security with "
        "`FORCE ROW LEVEL SECURITY` on authoritative tenant tables. Production runtime "
        "logins must be non-superuser roles without `BYPASSRLS`; migration/admin credentials "
        "are separate and are never application credentials.\n"
    )
    if old not in security:
        raise SystemExit("SECURITY tenant-RLS bullet drifted")
    _write(security_path, security.replace(old, new, 1))

    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path).rstrip()
    section = """

## Runtime database role boundary

All authoritative tenant tables use `FORCE ROW LEVEL SECURITY`. Run the service with a dedicated non-superuser PostgreSQL login that does not have `BYPASSRLS`; do not reuse the migration owner or an administrative credential for application traffic. The request/session boundary must set `app.tenant_account_id` from validated tenant authority before tenant-scoped SQL. Migration, restore, and break-glass administration use separate audited credentials and are not normal posting paths.
"""
    if "## Runtime database role boundary" not in operability:
        operability += section
    _write(operability_path, operability.rstrip() + "\n")


def main() -> None:
    """Apply forced-RLS storage and operating contracts exactly once."""
    add_force_rls_migration()
    apply_force_rls_migration()
    add_force_rls_regression()
    update_security_docs()


if __name__ == "__main__":
    main()
