"""One-shot repair for database-owned posted-ledger immutability in PR 2."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def add_database_immutability_guards() -> None:
    """Reject direct UPDATE or DELETE of authoritative posted-ledger facts."""
    path = "database/migrations/0005_closed_period_guard.sql"
    migration = _read(path)
    if "general_journal_immutable_guard" in migration:
        return
    anchor = "\nCOMMIT;\n"
    guard_sql = r'''

CREATE OR REPLACE FUNCTION accounting_core.reject_posted_ledger_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'posted accounting ledger facts are immutable (journal_immutable); post an explicit reversal and replacement instead'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE OR REPLACE TRIGGER general_journal_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.general_journal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_posted_ledger_mutation();

CREATE OR REPLACE TRIGGER journal_entry_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.journal_entry_line
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_posted_ledger_mutation();

CREATE OR REPLACE TRIGGER journal_source_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.journal_source_reference
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_posted_ledger_mutation();

CREATE OR REPLACE TRIGGER journal_reversal_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.journal_reversal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_posted_ledger_mutation();
'''
    if migration.count(anchor) != 1:
        raise SystemExit("closed-period migration COMMIT anchor drifted")
    _write(path, migration.replace(anchor, guard_sql + anchor, 1))


def add_database_immutability_regressions() -> None:
    """Prove privileged direct SQL cannot rewrite or delete posted facts."""
    path = "tests/test_postgres_posting.py"
    tests = _read(path)
    if "test_database_rejects_posted_journal_update" in tests:
        return
    marker = "    def _seed_master_data(self, *, period_status_code: str) -> str:\n"
    regressions = '''    def test_database_rejects_posted_journal_update(self) -> None:
        """A privileged direct UPDATE cannot rewrite an authoritative posted journal."""
        receipt = self.ledger.post(self._two_line_proposal(), self.policy)
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "journal_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.general_journal
                       SET journal_status_code = 'reversed'
                     WHERE tenant_account_id = %s
                       AND journal_reference = %s
                    """,
                    (self.tenant_id, receipt.journal_reference),
                )
            connection.rollback()
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)

    def test_database_rejects_posted_journal_line_delete(self) -> None:
        """A privileged direct DELETE cannot remove a posted journal line."""
        receipt = self.ledger.post(self._two_line_proposal(), self.policy)
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "journal_immutable",
            ):
                connection.execute(
                    """
                    DELETE FROM accounting_core.journal_entry_line
                     WHERE tenant_account_id = %s
                       AND general_journal_id = (
                           SELECT general_journal_id
                             FROM accounting_core.general_journal
                            WHERE tenant_account_id = %s
                              AND journal_reference = %s
                       )
                       AND line_number = 1
                    """,
                    (self.tenant_id, self.tenant_id, receipt.journal_reference),
                )
            connection.rollback()
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 2)

'''
    if marker not in tests:
        raise SystemExit("PostgreSQL immutability test insertion marker drifted")
    _write(path, tests.replace(marker, regressions + marker, 1))


def update_immutability_documentation() -> None:
    """Describe PostgreSQL as the final append-only enforcement boundary."""
    adr_path = "docs/adr/0003-append-only-journals.md"
    adr = _read(adr_path)
    old = """Checked-in PostgreSQL migrations cannot `UPDATE`, `DELETE`, `TRUNCATE`, or `DROP TABLE` `general_journal` or `journal_entry_line`, including schema-qualified and quoted identifiers. `scripts/validate_repository.py` rejects those statements so later schema work cannot rewrite posted journals and still pass CI. Destructive statements against unrelated tables remain valid.\n"""
    new = """Checked-in PostgreSQL migrations cannot `UPDATE`, `DELETE`, `TRUNCATE`, or `DROP TABLE` `general_journal` or `journal_entry_line`, including schema-qualified and quoted identifiers. `scripts/validate_repository.py` rejects those statements so later schema work cannot rewrite posted journals and still pass CI. Destructive statements against unrelated tables remain valid. PostgreSQL itself is also fail-closed: `reject_posted_ledger_mutation` guards `general_journal`, `journal_entry_line`, `journal_source_reference`, and `journal_reversal` against direct `UPDATE` or `DELETE`, including privileged application SQL. Corrections therefore remain append-only reversal/reposting operations rather than row mutation.\n"""
    if old not in adr:
        raise SystemExit("ADR 0003 append-only database paragraph drifted")
    _write(adr_path, adr.replace(old, new, 1))

    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path)
    anchor = """PostgreSQL deferred constraint triggers verify the complete journal population when a transaction commits. Every durable `general_journal` must contain lines with exactly equal debit and credit totals. A direct-SQL mutation that leaves a journal empty or unbalanced fails with `journal_unbalanced`; repair the transaction before retrying rather than disabling the trigger.\n"""
    replacement = anchor + "\nDirect `UPDATE` or `DELETE` against posted journal headers, lines, source references, or reversal links fails with `journal_immutable`. Correct accounting facts by posting an explicit reversal and replacement; never disable the immutability trigger as an operational repair.\n"
    if anchor not in operability:
        raise SystemExit("OPERABILITY database-invariant paragraph drifted")
    _write(operability_path, operability.replace(anchor, replacement, 1))


def main() -> None:
    """Apply the database immutability repair exactly once."""
    add_database_immutability_guards()
    add_database_immutability_regressions()
    update_immutability_documentation()


if __name__ == "__main__":
    main()
