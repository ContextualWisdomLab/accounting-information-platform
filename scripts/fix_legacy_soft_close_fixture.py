"""Correct the generated legacy soft-close PostgreSQL fixture after observing its RED boundary."""

from __future__ import annotations

from pathlib import Path


path = Path(__file__).resolve().parents[1] / "tests/test_period_close_book_scope.py"
text = path.read_text(encoding="utf-8")
old = '''        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                UPDATE accounting_core.accounting_book_period_control AS period_control
                SET period_status_code = 'soft_closed', period_closed_at = clock_timestamp()
                FROM accounting_core.accounting_book AS accounting_book,
                     accounting_core.fiscal_period AS fiscal_period,
                     accounting_core.tenant_account AS tenant_account
                WHERE period_control.tenant_account_id = tenant_account.tenant_account_id
                  AND period_control.accounting_book_id = accounting_book.accounting_book_id
                  AND period_control.fiscal_period_id = fiscal_period.fiscal_period_id
                  AND tenant_account.tenant_account_code = %s
                  AND accounting_book.book_name = %s
                  AND fiscal_period.period_code = '2026-08'
                """,
                (self.tenant_reference, self.stat_book_reference),
            )
'''
new = '''        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.accounting_book_period_control (
                    tenant_account_id,
                    accounting_book_id,
                    fiscal_period_id,
                    period_status_code,
                    period_closed_at
                )
                SELECT tenant_account.tenant_account_id,
                       accounting_book.accounting_book_id,
                       fiscal_period.fiscal_period_id,
                       'soft_closed',
                       clock_timestamp()
                FROM accounting_core.tenant_account AS tenant_account
                JOIN accounting_core.accounting_book AS accounting_book
                  ON accounting_book.tenant_account_id = tenant_account.tenant_account_id
                JOIN accounting_core.fiscal_period AS fiscal_period
                  ON fiscal_period.tenant_account_id = tenant_account.tenant_account_id
                WHERE tenant_account.tenant_account_code = %s
                  AND accounting_book.book_name = %s
                  AND fiscal_period.period_code = '2026-08'
                """,
                (self.tenant_reference, self.stat_book_reference),
            )
'''
if text.count(old) != 1:
    raise SystemExit("generated legacy soft-close fixture drifted")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
