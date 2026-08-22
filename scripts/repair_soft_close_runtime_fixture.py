"""One-shot fixture repair for soft-close capability tests after DB-owned tenant binding."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    """Bind temporary close-capability login roles to the fixture tenant and clean them up."""
    path = Path("tests/test_postgres_invariant_boundaries.py")
    text = path.read_text(encoding="utf-8")

    old_setup = """            admin.execute(
                sql.SQL(\"GRANT accounting_closing_writer TO {}\").format(
                    sql.Identifier(closer_role)
                )
            )
"""
    new_setup = old_setup + """            for role_name in (plain_role, closer_role):
                role_oid = admin.execute(
                    \"SELECT oid FROM pg_catalog.pg_roles WHERE rolname = %s\",
                    (role_name,),
                ).fetchone()[0]
                admin.execute(
                    \"\"\"
                    INSERT INTO accounting_core.runtime_tenant_binding (
                        runtime_role_oid,
                        runtime_role_name,
                        tenant_account_id
                    ) VALUES (%s, %s, %s)
                    \"\"\",
                    (role_oid, role_name, self.case.tenant_id),
                )
"""
    if text.count(old_setup) != 1:
        raise SystemExit("soft-close role setup anchor drifted")
    text = text.replace(old_setup, new_setup, 1)

    old_doc = """    def _bind_soft_close_session(self, connection: psycopg.Connection) -> None:
        \"\"\"Bind tenant and the caller-controlled classification GUC.\"\"\"
"""
    new_doc = """    def _bind_soft_close_session(self, connection: psycopg.Connection) -> None:
        \"\"\"Set legacy tenant/classification GUCs while DB login binding remains authoritative.\"\"\"
"""
    if text.count(old_doc) != 1:
        raise SystemExit("soft-close session helper anchor drifted")
    text = text.replace(old_doc, new_doc, 1)

    old_cleanup = """        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL(\"REVOKE accounting_closing_writer FROM {}\").format(
                    sql.Identifier(closer_role)
                )
            )
"""
    new_cleanup = """        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                \"DELETE FROM accounting_core.runtime_tenant_binding \"
                \"WHERE runtime_role_name = ANY(%s)\",
                ([plain_role, closer_role],),
            )
            admin.execute(
                sql.SQL(\"REVOKE accounting_closing_writer FROM {}\").format(
                    sql.Identifier(closer_role)
                )
            )
"""
    if text.count(old_cleanup) != 1:
        raise SystemExit("soft-close cleanup anchor drifted")
    path.write_text(text.replace(old_cleanup, new_cleanup, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
