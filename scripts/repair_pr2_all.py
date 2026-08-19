"""Apply the complete reviewed PR 2 repair set in a deterministic order."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load(name: str) -> ModuleType:
    """Load one sibling repair module from this script's directory."""
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"unable to load repair module {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fix_reversal_key_after_reference_resolution() -> None:
    """Derive the default reversal key only after resolving the original reference."""
    path = Path("src/accounting_information_platform/accept.py")
    text = path.read_text(encoding="utf-8")
    premature = (
        "    reversal_idempotency_key = str(\n"
        "        payload.get(\"reversal_idempotency_key\") or f\"reversal:{journal_reference}\"\n"
        "    ).strip()\n"
        "    if not reversal_idempotency_key:\n"
        "        raise AccountingValidationError(\n"
        "            \"reversal_idempotency_key must not be empty\"\n"
        "        )\n"
        "    ledger = PostgresPostingLedger(database_url, tenant_reference)\n"
    )
    if premature not in text:
        raise SystemExit("reversal idempotency resolution anchor drifted")
    text = text.replace(
        premature,
        "    ledger = PostgresPostingLedger(database_url, tenant_reference)\n",
        1,
    )
    anchor = (
        "        journal_reference = resolved_reference\n"
        "    policy = ledger.load_reversal_policy(journal_reference, reversal_date)\n"
    )
    replacement = (
        "        journal_reference = resolved_reference\n"
        "    reversal_idempotency_key = str(\n"
        "        payload.get(\"reversal_idempotency_key\") or f\"reversal:{journal_reference}\"\n"
        "    ).strip()\n"
        "    if not reversal_idempotency_key:\n"
        "        raise AccountingValidationError(\n"
        "            \"reversal_idempotency_key must not be empty\"\n"
        "        )\n"
        "    policy = ledger.load_reversal_policy(journal_reference, reversal_date)\n"
    )
    if anchor not in text:
        raise SystemExit("resolved reversal idempotency anchor drifted")
    path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")


def normalize_postgres_regression_setup() -> None:
    """Grant the purpose-limited close role and keep direct-journal fixtures balanced."""
    path = Path("tests/test_postgres_posting.py")
    text = path.read_text(encoding="utf-8")
    migration_anchor = "        apply_foundation_migration(DATABASE_URL, MIGRATION_PATH)\n"
    migration_replacement = migration_anchor + '''        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            runtime_role = connection.execute("SELECT current_user").fetchone()[0]
            connection.execute(
                sql.SQL("GRANT accounting_closing_writer TO {}").format(
                    sql.Identifier(runtime_role)
                )
            )
'''
    if text.count(migration_anchor) != 1:
        raise SystemExit("PostgreSQL test migration anchor drifted")
    text = text.replace(migration_anchor, migration_replacement, 1)

    start = text.index("    def _raw_insert_general_journal(")
    end = text.index("    def _close_period(", start)
    helper = text[start:end]
    commit_anchor = "            connection.commit()\n"
    if helper.count(commit_anchor) != 1:
        raise SystemExit("raw journal helper commit anchor drifted")
    balanced_lines = '''            journal_id = connection.execute(
                """
                SELECT general_journal_id
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (self.tenant_id, journal_reference),
            ).fetchone()[0]
            account_ids = dict(
                connection.execute(
                    """
                    SELECT chart_account_code, chart_account_id
                    FROM accounting_core.chart_account
                    WHERE tenant_account_id = %s
                      AND chart_account_code IN ('110100', '410100')
                      AND valid_to IS NULL
                    """,
                    (self.tenant_id,),
                ).fetchall()
            )
            for line_number, account_code, role_code, debit_amount, credit_amount in (
                (1, "110100", "accounts_receivable", Decimal("1"), Decimal("0")),
                (2, "410100", "usage_revenue", Decimal("0"), Decimal("1")),
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.journal_entry_line (
                        tenant_account_id, general_journal_id, line_number,
                        chart_account_id, account_role_code, debit_amount, credit_amount
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.tenant_id,
                        journal_id,
                        line_number,
                        account_ids[account_code],
                        role_code,
                        debit_amount,
                        credit_amount,
                    ),
                )
            connection.commit()
'''
    helper = helper.replace(commit_anchor, balanced_lines, 1)
    path.write_text(text[:start] + helper + text[end:], encoding="utf-8")


def update_final_release_notes() -> None:
    """Record the database-owned accounting security and immutability boundary."""
    path = Path("CHANGELOG.md")
    text = path.read_text(encoding="utf-8")
    entries = (
        "- Enforced posted-ledger immutability in PostgreSQL itself: direct UPDATE or DELETE of journal headers, lines, source references, and reversal links now fails closed; corrections remain reversal/reposting operations.\n",
        "- Forced row-level security on authoritative tenant tables and documented a separate non-superuser, non-BYPASSRLS application-role boundary from migration and break-glass administration.\n",
        "- Rejected reversal commands whose accounting date precedes the original journal, preventing a correcting entry from appearing before the fact it reverses.\n",
        "- Preserved required adjusting-journal descriptions as durable header evidence, accepted only exact decimal-string monetary input, and fail-closed unknown period-close target states.\n",
    )
    marker = "### Changed\n"
    if marker not in text:
        raise SystemExit("CHANGELOG changed-section anchor drifted")
    for entry in reversed(entries):
        if entry not in text:
            text = text.replace(marker, marker + "\n" + entry, 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    """Apply all causal repairs before full validation and publication."""
    ledger = _load("repair_pr2_ledger_invariants")
    ledger.replace_period_guard_migration()
    ledger.add_database_regression_tests()
    ledger.update_documentation()

    command = _load("repair_pr2_command_idempotency")
    command.main()
    fix_reversal_key_after_reference_resolution()
    _load("repair_pr2_reversal_temporal_order").main()
    _load("repair_pr2_remaining_review_contracts").main()

    followup = _load("repair_pr2_followup_adjustments")
    followup.retain_tenant_defense_on_reversal_cache()
    followup.harden_home_tax_projection()
    followup.normalize_trigger_and_test_cleanup()

    normalize_postgres_regression_setup()

    _load("repair_pr2_database_immutability").main()
    _load("repair_pr2_home_tax_replay_outcome").main()
    _load("repair_pr2_force_rls").main()
    update_final_release_notes()


if __name__ == "__main__":
    main()
