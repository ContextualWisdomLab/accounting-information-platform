"""Reject pre-closed fiscal-period creation from ordinary runtime SQL."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def enforce_initial_period_state() -> None:
    """Require every newly inserted fiscal period to begin open and unclosed."""
    path = "database/migrations/0007_runtime_tenant_binding.sql"
    text = _read(path)
    if "fiscal_period_initial_state_guard" in text:
        return
    anchor = "\nCOMMIT;\n"
    guard = r'''

CREATE OR REPLACE FUNCTION accounting_core.guard_fiscal_period_initial_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.period_status_code <> 'open' OR NEW.period_closed_at IS NOT NULL THEN
        RAISE EXCEPTION
            'new fiscal periods must begin open and unclosed (period_initial_state); create the open period first, then use the controlled close command'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER fiscal_period_initial_state_guard
    BEFORE INSERT ON accounting_core.fiscal_period
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_fiscal_period_initial_state();
'''
    if text.count(anchor) != 1:
        raise SystemExit("runtime-tenant migration COMMIT anchor drifted")
    _write(path, text.replace(anchor, guard + anchor, 1))


def add_runtime_regression() -> None:
    """Prove a real ordinary runtime login cannot manufacture a pre-closed period."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)
    if "period_initial_state" in text:
        return
    anchor = '''            self.assertEqual(
                connection.execute(
                    "SELECT tenant_account_id FROM accounting_core.tenant_account"
                ).fetchall(),
                [(self.tenant_id,)],
            )
'''
    replacement = anchor + '''            fiscal_calendar_id = connection.execute(
                """
                SELECT fiscal_calendar_id
                  FROM accounting_core.fiscal_calendar
                 WHERE tenant_account_id = %s
                 ORDER BY created_at, fiscal_calendar_id
                 LIMIT 1
                """,
                (self.tenant_id,),
            ).fetchone()[0]
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "period_initial_state",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.fiscal_period (
                        tenant_account_id, fiscal_calendar_id, period_code,
                        period_start_date, period_end_date, period_status_code,
                        period_closed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 'hard_closed', clock_timestamp())
                    """,
                    (
                        self.tenant_id,
                        fiscal_calendar_id,
                        f"runtime-closed-{uuid.uuid4().hex[:8]}",
                        date(2099, 1, 1),
                        date(2099, 1, 31),
                    ),
                )
            connection.rollback()
'''
    if anchor not in text:
        raise SystemExit("runtime period-insert regression anchor drifted")
    _write(path, text.replace(anchor, replacement, 1))


def update_docs() -> None:
    """Record the database-owned initial-period state contract."""
    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path).rstrip()
    sentence = (
        " New fiscal-period rows are database-guarded to start `open` with no "
        "`period_closed_at`; a runtime connection cannot manufacture a pre-closed period "
        "and bypass the close evidence path."
    )
    if sentence.strip() not in operability:
        marker = "## Purpose-limited close connection"
        if marker in operability:
            operability += "\n" + sentence.strip() + "\n"
        else:
            operability += "\n\n## Fiscal-period state boundary\n\n" + sentence.strip() + "\n"
    _write(operability_path, operability.rstrip() + "\n")

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    entry = "- Added a PostgreSQL fiscal-period initial-state guard so direct runtime SQL cannot create a period already soft- or hard-closed and bypass controlled close evidence.\n"
    if entry not in changelog:
        marker = "### Security\n"
        if marker in changelog:
            changelog = changelog.replace(marker, marker + "\n" + entry, 1)
        else:
            changelog = changelog.rstrip() + "\n\n### Security\n\n" + entry
    _write(changelog_path, changelog)


def main() -> None:
    """Apply fiscal-period initial-state DB guard, runtime regression, and docs."""
    enforce_initial_period_state()
    add_runtime_regression()
    update_docs()


if __name__ == "__main__":
    main()
