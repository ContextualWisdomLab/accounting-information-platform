"""Bind tenant RLS to a real admin-provisioned runtime database login."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def add_runtime_tenant_binding_migration() -> None:
    """Make runtime tenant authority independent of request-controlled session GUCs."""
    path = Path("database/migrations/0007_runtime_tenant_binding.sql")
    if path.exists():
        return
    migration = r'''BEGIN;

DO $role_setup$
BEGIN
    IF to_regrole('accounting_runtime_user') IS NULL THEN
        CREATE ROLE accounting_runtime_user NOLOGIN;
    END IF;
END
$role_setup$;

CREATE TABLE accounting_core.runtime_tenant_binding (
    runtime_tenant_binding_id uuid PRIMARY KEY DEFAULT uuidv7(),
    runtime_database_role name NOT NULL UNIQUE,
    tenant_account_id uuid NOT NULL REFERENCES accounting_core.tenant_account (tenant_account_id),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_account_id, runtime_tenant_binding_id)
);

REVOKE ALL ON accounting_core.runtime_tenant_binding FROM PUBLIC;
REVOKE ALL ON accounting_core.runtime_tenant_binding FROM accounting_runtime_user;

CREATE OR REPLACE FUNCTION accounting_core.current_tenant_account_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
    SELECT CASE
        WHEN pg_has_role(session_user, 'accounting_runtime_user', 'MEMBER') THEN (
            SELECT runtime_binding.tenant_account_id
            FROM accounting_core.runtime_tenant_binding AS runtime_binding
            WHERE runtime_binding.runtime_database_role = session_user::name
        )
        ELSE nullif(current_setting('app.tenant_account_id', true), '')::uuid
    END
$$;

ALTER TABLE accounting_core.tenant_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.tenant_account FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_account_isolation ON accounting_core.tenant_account
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

GRANT USAGE ON SCHEMA accounting_core, accounting_integration, accounting_reporting
    TO accounting_runtime_user;
GRANT EXECUTE ON FUNCTION accounting_core.current_tenant_account_id()
    TO accounting_runtime_user;

GRANT SELECT ON accounting_core.tenant_account,
                accounting_core.legal_entity_record,
                accounting_core.accounting_book,
                accounting_core.chart_account,
                accounting_core.account_role_mapping,
                accounting_core.fiscal_calendar,
                accounting_core.fiscal_period,
                accounting_core.general_journal,
                accounting_core.journal_entry_line,
                accounting_core.journal_source_reference,
                accounting_core.journal_reversal,
                accounting_integration.journal_proposal_record,
                accounting_integration.posting_receipt,
                accounting_integration.outbox_event,
                accounting_integration.home_tax_submission,
                accounting_reporting.trial_balance_snapshot,
                accounting_reporting.trial_balance_line
    TO accounting_runtime_user;

GRANT INSERT ON accounting_core.fiscal_calendar,
                accounting_core.fiscal_period,
                accounting_core.general_journal,
                accounting_core.journal_entry_line,
                accounting_core.journal_source_reference,
                accounting_core.journal_reversal,
                accounting_integration.journal_proposal_record,
                accounting_integration.posting_receipt,
                accounting_integration.outbox_event,
                accounting_integration.home_tax_submission,
                accounting_reporting.trial_balance_snapshot,
                accounting_reporting.trial_balance_line
    TO accounting_runtime_user;

GRANT UPDATE ON accounting_core.fiscal_period,
                accounting_integration.journal_proposal_record,
                accounting_integration.outbox_event
    TO accounting_runtime_user;

COMMIT;
'''
    path.write_text(migration, encoding="utf-8")


def extend_foundation_installer() -> None:
    """Apply the runtime-tenant binding after forced RLS on every clean install."""
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)
    force_check = '''    force_rls_migration_path = migration_path.parent / "0006_force_tenant_rls.sql"
    if not force_rls_migration_path.is_file():
        raise AccountingValidationError(
            f"Forced-RLS migration is missing at {force_rls_migration_path}. "
            "Restore database/migrations/0006_force_tenant_rls.sql, then retry."
        )
    psycopg = _import_psycopg()
'''
    runtime_check = '''    force_rls_migration_path = migration_path.parent / "0006_force_tenant_rls.sql"
    if not force_rls_migration_path.is_file():
        raise AccountingValidationError(
            f"Forced-RLS migration is missing at {force_rls_migration_path}. "
            "Restore database/migrations/0006_force_tenant_rls.sql, then retry."
        )
    runtime_tenant_migration_path = migration_path.parent / "0007_runtime_tenant_binding.sql"
    if not runtime_tenant_migration_path.is_file():
        raise AccountingValidationError(
            f"Runtime-tenant binding migration is missing at {runtime_tenant_migration_path}. "
            "Restore database/migrations/0007_runtime_tenant_binding.sql, then retry."
        )
    psycopg = _import_psycopg()
'''
    if runtime_check not in text:
        if force_check not in text:
            raise SystemExit("runtime-tenant migration path anchor drifted")
        text = text.replace(force_check, runtime_check, 1)

    execute_anchor = '''            connection.execute(period_guard_migration_path.read_text(encoding="utf-8"))
            connection.execute(force_rls_migration_path.read_text(encoding="utf-8"))
'''
    execute_replacement = execute_anchor + '''            connection.execute(runtime_tenant_migration_path.read_text(encoding="utf-8"))
'''
    if "connection.execute(runtime_tenant_migration_path.read_text" not in text:
        if execute_anchor not in text:
            raise SystemExit("runtime-tenant migration execution anchor drifted")
        text = text.replace(execute_anchor, execute_replacement, 1)
    _write(path, text)


def _ensure_test_imports() -> None:
    """Provide cryptographic test credentials and psycopg SQL composition helpers."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)
    if "import secrets\n" not in text:
        text = text.replace("import os\n", "import os\nimport secrets\n", 1)
    if "from psycopg import sql\n" not in text:
        marker = "import psycopg\n"
        if marker not in text:
            raise SystemExit("psycopg import anchor drifted")
        text = text.replace(marker, marker + "from psycopg import sql\n", 1)
    _write(path, text)


def add_runtime_identity_regression() -> None:
    """Prove a real non-owner login is tenant-bound and cannot bypass ledger controls."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)
    if "test_real_runtime_login_is_tenant_bound_and_cannot_bypass_controls" in text:
        return
    marker = "    def _seed_master_data(self, *, period_status_code: str) -> str:\n"
    regression = '''    def test_real_runtime_login_is_tenant_bound_and_cannot_bypass_controls(self) -> None:
        """A real LOGIN uses RLS safely and cannot gain close or mutation authority from GUCs."""
        runtime_role = f"accounting_runtime_{uuid.uuid4().hex[:12]}"
        runtime_password = secrets.token_urlsafe(24)
        other_tenant_code = f"urn:cwl:tenant_other_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(DATABASE_URL, autocommit=True) as administrator:
            other_tenant_id = administrator.execute(
                """
                INSERT INTO accounting_core.tenant_account (tenant_account_code)
                VALUES (%s)
                RETURNING tenant_account_id
                """,
                (other_tenant_code,),
            ).fetchone()[0]

        def cleanup_runtime_identity() -> None:
            with psycopg.connect(DATABASE_URL, autocommit=True) as administrator:
                administrator.execute(
                    "DELETE FROM accounting_core.runtime_tenant_binding WHERE runtime_database_role = %s",
                    (runtime_role,),
                )
                administrator.execute(
                    "DELETE FROM accounting_core.legal_entity_record WHERE tenant_account_id = %s",
                    (other_tenant_id,),
                )
                administrator.execute(
                    "DELETE FROM accounting_core.tenant_account WHERE tenant_account_id = %s",
                    (other_tenant_id,),
                )
                if administrator.execute(
                    "SELECT to_regrole(%s)", (runtime_role,)
                ).fetchone()[0] is not None:
                    administrator.execute(
                        sql.SQL("REVOKE accounting_runtime_user FROM {}").format(
                            sql.Identifier(runtime_role)
                        )
                    )
                    administrator.execute(
                        sql.SQL("DROP OWNED BY {}").format(sql.Identifier(runtime_role))
                    )
                    administrator.execute(
                        sql.SQL("DROP ROLE {}").format(sql.Identifier(runtime_role))
                    )

        self.addCleanup(cleanup_runtime_identity)

        with psycopg.connect(DATABASE_URL, autocommit=True) as administrator:
            administrator.execute(
                """
                INSERT INTO accounting_core.legal_entity_record (
                    tenant_account_id, legal_entity_code, entity_name,
                    functional_currency_code, valid_from
                )
                VALUES (%s, 'OTHER-ENTITY', 'Other Tenant Entity', 'KRW', clock_timestamp())
                """,
                (other_tenant_id,),
            )
            administrator.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "INHERIT NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(runtime_role), sql.Literal(runtime_password))
            )
            administrator.execute(
                sql.SQL("GRANT accounting_runtime_user TO {}").format(
                    sql.Identifier(runtime_role)
                )
            )
            administrator.execute(
                """
                INSERT INTO accounting_core.runtime_tenant_binding (
                    runtime_database_role, tenant_account_id
                )
                VALUES (%s, %s)
                """,
                (runtime_role, self.tenant_id),
            )
            role_flags = administrator.execute(
                """
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolbypassrls
                FROM pg_catalog.pg_roles
                WHERE rolname = %s
                """,
                (runtime_role,),
            ).fetchone()
            self.assertEqual(role_flags, (False, False, False, True, False))

        runtime_url = psycopg.conninfo.make_conninfo(
            DATABASE_URL,
            user=runtime_role,
            password=runtime_password,
        )
        runtime_ledger = PostgresPostingLedger(
            runtime_url,
            tenant_reference=self.policy.tenant_reference,
        )
        runtime_proposal = self._two_line_proposal(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=f"runtime-login:{uuid.uuid4()}",
            source_payload_hash="sha256:" + "9" * 64,
        )
        runtime_receipt = runtime_ledger.post(runtime_proposal, self.policy)
        self.assertEqual(runtime_receipt.posting_status_code, "posted")

        with psycopg.connect(runtime_url) as connection:
            identity = connection.execute(
                "SELECT session_user, current_user, accounting_core.current_tenant_account_id()::text"
            ).fetchone()
            self.assertEqual(identity, (runtime_role, runtime_role, self.tenant_id))
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(other_tenant_id),),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT accounting_core.current_tenant_account_id()::text"
                ).fetchone()[0],
                self.tenant_id,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT tenant_account_id::text FROM accounting_core.tenant_account"
                ).fetchall(),
                [(self.tenant_id,)],
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT count(*)
                    FROM accounting_core.legal_entity_record
                    WHERE tenant_account_id = %s
                    """,
                    (other_tenant_id,),
                ).fetchone()[0],
                0,
            )
            own_period_write = connection.execute(
                """
                UPDATE accounting_core.fiscal_period
                   SET recorded_at = recorded_at
                 WHERE tenant_account_id = %s
                RETURNING fiscal_period_id
                """,
                (self.tenant_id,),
            ).fetchone()
            self.assertIsNotNone(own_period_write)
            self.assertIsNone(
                connection.execute(
                    """
                    UPDATE accounting_core.fiscal_period
                       SET recorded_at = recorded_at
                     WHERE tenant_account_id = %s
                    RETURNING fiscal_period_id
                    """,
                    (other_tenant_id,),
                ).fetchone()
            )

        self._set_period_status("soft_closed")
        with psycopg.connect(runtime_url) as connection:
            connection.execute(
                "SELECT set_config('accounting_core.journal_write_role', 'adjusting', false)"
            )
            legal_entity_id, book_id, period_id = connection.execute(
                """
                SELECT legal_entity_record.legal_entity_id,
                       accounting_book.accounting_book_id,
                       fiscal_period.fiscal_period_id
                FROM accounting_core.legal_entity_record
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id = legal_entity_record.tenant_account_id
                 AND accounting_book.legal_entity_id = legal_entity_record.legal_entity_id
                JOIN accounting_core.fiscal_period
                  ON fiscal_period.tenant_account_id = legal_entity_record.tenant_account_id
                WHERE legal_entity_record.tenant_account_id = %s
                  AND fiscal_period.period_code = '2026-08'
                """,
                (self.tenant_id,),
            ).fetchone()
            proposal_record_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, uuidv7(), 1, %s, %s, 'posted', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (
                    self.tenant_id,
                    f"runtime-bypass:{uuid.uuid4()}",
                    "sha256:" + "8" * 64,
                ),
            ).fetchone()[0]
            with self.assertRaisesRegex(psycopg.Error, "period_closed"):
                connection.execute(
                    """
                    INSERT INTO accounting_core.general_journal (
                        tenant_account_id, legal_entity_id, accounting_book_id, fiscal_period_id,
                        journal_reference, journal_status_code, transaction_currency_code,
                        functional_currency_code, transaction_date, accounting_date,
                        source_proposal_record_id, accounting_policy_version, posting_rule_version
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, 'posted', 'KRW', 'KRW', %s, %s, %s,
                        'ifrs-v1', 'billing-issued-v1'
                    )
                    """,
                    (
                        self.tenant_id,
                        legal_entity_id,
                        book_id,
                        period_id,
                        f"urn:cwl:accounting:general_journal:{uuid.uuid4()}",
                        date(2026, 8, 31),
                        date(2026, 8, 31),
                        proposal_record_id,
                    ),
                )
            connection.rollback()

        self._set_period_status("open")
        with psycopg.connect(runtime_url) as connection:
            with self.assertRaisesRegex(
                psycopg.Error, "journal_immutable|permission denied for table general_journal"
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.general_journal
                       SET journal_status_code = 'reversed'
                     WHERE tenant_account_id = %s
                       AND journal_reference = %s
                    """,
                    (self.tenant_id, runtime_receipt.journal_reference),
                )
            connection.rollback()

'''
    if marker not in text:
        raise SystemExit("runtime-tenant PostgreSQL test insertion marker drifted")
    _write(path, text.replace(marker, regression + marker, 1))


def update_docs() -> None:
    """Document that the tenant GUC is compatibility metadata, not runtime authority."""
    security_path = "docs/SECURITY.md"
    security = _read(security_path).rstrip()
    section = '''

## Database runtime tenant authority

Production application logins are direct PostgreSQL `LOGIN` roles, are members of the NOLOGIN `accounting_runtime_user` privilege role, and are provisioned in `accounting_core.runtime_tenant_binding` by a migration/admin identity. They are non-superuser, non-`BYPASSRLS`, non-owner roles. For those runtime members, `accounting_core.current_tenant_account_id()` derives tenant authority from immutable `session_user` membership/binding and ignores caller changes to `app.tenant_account_id`. The legacy GUC remains an administrator/test compatibility path only for sessions that are not application-runtime members. Runtime roles have no privileges on `runtime_tenant_binding` and cannot rebind themselves. `tenant_account` itself is RLS-protected so a runtime login cannot enumerate sibling tenants.
'''
    if "## Database runtime tenant authority" not in security:
        security += section
    _write(security_path, security.rstrip() + "\n")

    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path).rstrip()
    section = '''

## Runtime tenant provisioning

Create a dedicated PostgreSQL `LOGIN` for each application runtime identity outside normal request handling, ensure it is non-superuser and does not have `BYPASSRLS`, grant it membership in `accounting_runtime_user`, and insert exactly one `runtime_tenant_binding` row through an audited migration/admin session. Do not grant the runtime login privileges on that binding table, role administration, migration ownership, or break-glass roles. The HTTP server tenant binding and authenticated token tenant must match the tenant mapped to the database `session_user`; a mismatch fails before accounting work rather than switching RLS with a request-controlled GUC. Integration tests connect as an actual runtime login and exercise supported posting/read paths plus cross-tenant, soft-close, and immutable-ledger denial paths.
'''
    if "## Runtime tenant provisioning" not in operability:
        operability += section
    _write(operability_path, operability.rstrip() + "\n")

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    entry = "- Bound production tenant RLS to a real admin-provisioned, non-owner PostgreSQL runtime login and verified supported posting/read paths plus cross-tenant, soft-close, and immutable-ledger denials without relying on caller-controlled tenant GUCs.\n"
    if entry not in changelog:
        marker = "### Security\n"
        if marker in changelog:
            changelog = changelog.replace(marker, marker + "\n" + entry, 1)
        else:
            changelog = changelog.rstrip() + "\n\n### Security\n\n" + entry
    _write(changelog_path, changelog)


def main() -> None:
    """Install runtime-role tenant binding, real-login regression evidence, and docs."""
    add_runtime_tenant_binding_migration()
    extend_foundation_installer()
    _ensure_test_imports()
    add_runtime_identity_regression()
    update_docs()


if __name__ == "__main__":
    main()
