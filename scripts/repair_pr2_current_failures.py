"""Repair only the current PR 2 failures after database invariants landed."""

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


def _document_current_reversal_contract() -> None:
    """Keep current exact-replay prose and add the temporal ordering invariant."""
    adr_path = Path("docs/adr/0003-append-only-journals.md")
    adr = adr_path.read_text(encoding="utf-8")
    temporal_sentence = (
        " A reversal accounting date must also be on or after the original journal "
        "accounting date, so a reversal cannot appear in a trial balance before the "
        "fact it corrects."
    )
    if temporal_sentence.strip() not in adr:
        anchor = (
            "A reversal replay is valid only when the tenant, reversal command "
            "idempotency key, original journal reference, and immutable "
            "reversal-command payload hash all match the stored reversal command; "
            "any mismatch fails closed."
        )
        if anchor not in adr:
            raise SystemExit("current reversal ADR anchor drifted")
        adr = adr.replace(anchor, anchor + temporal_sentence, 1)
        adr_path.write_text(adr, encoding="utf-8")

    changelog_path = Path("CHANGELOG.md")
    changelog = changelog_path.read_text(encoding="utf-8")
    entry = (
        "- Made HomeTax rejection commands and journal reversals exactly idempotent: "
        "tenant-scoped command keys replay only identical command evidence, while "
        "changed evidence fails closed; reversals also cannot predate the original "
        "accounting date.\n"
    )
    if entry not in changelog:
        marker = "### Changed\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG changed-section anchor drifted")
        changelog_path.write_text(
            changelog.replace(marker, marker + "\n" + entry, 1),
            encoding="utf-8",
        )


def _normalize_postgres_regression_setup() -> None:
    """Patch only the canonical fixture setup and direct-journal helper."""
    path = Path("tests/test_postgres_posting.py")
    text = path.read_text(encoding="utf-8")
    sql_import = "import psycopg\nfrom psycopg import sql\n"
    if sql_import not in text:
        import_anchor = "import psycopg\n"
        if import_anchor not in text:
            raise SystemExit("PostgreSQL test psycopg import anchor drifted")
        text = text.replace(import_anchor, sql_import, 1)

    setup_start = text.index("    @classmethod\n    def setUpClass(cls) -> None:\n")
    setup_end = text.index("\n    def setUp(self) -> None:\n", setup_start)
    setup = text[setup_start:setup_end]
    migration_anchor = "        apply_foundation_migration(DATABASE_URL, MIGRATION_PATH)\n"
    grant_block = '''        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            runtime_role = connection.execute("SELECT current_user").fetchone()[0]
            connection.execute(
                sql.SQL("GRANT accounting_closing_writer TO {}").format(
                    sql.Identifier(runtime_role)
                )
            )
'''
    if grant_block not in setup:
        if setup.count(migration_anchor) != 1:
            raise SystemExit("PostgreSQL setUpClass migration anchor drifted")
        setup = setup.replace(migration_anchor, migration_anchor + grant_block, 1)
        text = text[:setup_start] + setup + text[setup_end:]

    helper_start = text.index("    def _raw_insert_general_journal(")
    helper_end = text.index("    def _close_period(", helper_start)
    helper = text[helper_start:helper_end]
    if "account_ids = dict(" not in helper and "chart_accounts = dict(" not in helper:
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
        text = text[:helper_start] + helper + text[helper_end:]

    path.write_text(text, encoding="utf-8")


def _ensure_operability_database_invariant_anchor() -> None:
    """Restore the code-current journal-balance runbook paragraph when predecessor prose drifted."""
    path = Path("docs/OPERABILITY.md")
    text = path.read_text(encoding="utf-8")
    anchor = (
        "PostgreSQL deferred constraint triggers verify the complete journal population "
        "when a transaction commits. Every durable `general_journal` must contain lines "
        "with exactly equal debit and credit totals. A direct-SQL mutation that leaves a "
        "journal empty or unbalanced fails with `journal_unbalanced`; repair the transaction "
        "before retrying rather than disabling the trigger.\n"
    )
    if anchor in text:
        return
    marker = "\n## Initial service objectives\n"
    if marker not in text:
        raise SystemExit("OPERABILITY service-objectives anchor drifted")
    path.write_text(text.replace(marker, "\n" + anchor + marker, 1), encoding="utf-8")


def main() -> None:
    """Repair current HomeTax, reversal, and database-regression failures."""
    command = _load("repair_pr2_command_idempotency")
    command.update_home_tax_contract()
    command.update_reversal_contract()
    command.update_tests()

    aggregate = _load("repair_pr2_all")
    aggregate.fix_reversal_key_after_reference_resolution()

    temporal = _load("repair_pr2_reversal_temporal_order")
    temporal.harden_reference_oracle()
    temporal.harden_postgres_reversal()
    temporal.add_regressions()
    temporal.add_authority_edge_regression()

    followup = _load("repair_pr2_followup_adjustments")
    followup.harden_home_tax_projection()
    followup.normalize_trigger_and_test_cleanup()

    _load("repair_pr2_home_tax_replay_outcome").main()
    _normalize_postgres_regression_setup()
    _ensure_operability_database_invariant_anchor()
    _document_current_reversal_contract()


if __name__ == "__main__":
    main()
