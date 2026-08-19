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
    """Reject mutation or post-finalization extension of authoritative ledger facts."""
    path = "database/migrations/0005_closed_period_guard.sql"
    migration = _read(path)
    if "journal_entry_finalized_guard" in migration:
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

CREATE OR REPLACE FUNCTION accounting_core.reject_finalized_journal_content_append()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM accounting_integration.posting_receipt
         WHERE posting_receipt.tenant_account_id = NEW.tenant_account_id
           AND posting_receipt.general_journal_id = NEW.general_journal_id
    ) THEN
        RAISE EXCEPTION
            'posted journal content is finalized (journal_finalized); post an explicit reversal and replacement instead'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reject_finalized_reversal_append()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM accounting_integration.posting_receipt
         WHERE posting_receipt.tenant_account_id = NEW.tenant_account_id
           AND posting_receipt.general_journal_id = NEW.reversal_journal_id
    ) THEN
        RAISE EXCEPTION
            'posted reversal lineage is finalized (journal_finalized); post a new correcting journal instead'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reject_finalized_proposal_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM accounting_integration.posting_receipt
         WHERE posting_receipt.tenant_account_id = OLD.tenant_account_id
           AND posting_receipt.proposal_record_id = OLD.proposal_record_id
    ) THEN
        RAISE EXCEPTION
            'accounting command evidence is finalized (journal_finalized); record a new correcting command instead'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
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

CREATE OR REPLACE TRIGGER posting_receipt_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.posting_receipt
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_posted_ledger_mutation();

CREATE OR REPLACE TRIGGER journal_proposal_finalized_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.journal_proposal_record
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_finalized_proposal_mutation();

CREATE OR REPLACE TRIGGER journal_entry_finalized_guard
    BEFORE INSERT ON accounting_core.journal_entry_line
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_finalized_journal_content_append();

CREATE OR REPLACE TRIGGER journal_source_finalized_guard
    BEFORE INSERT ON accounting_core.journal_source_reference
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_finalized_journal_content_append();

CREATE OR REPLACE TRIGGER journal_reversal_finalized_guard
    BEFORE INSERT ON accounting_core.journal_reversal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_finalized_reversal_append();
'''
    if migration.count(anchor) != 1:
        raise SystemExit("closed-period migration COMMIT anchor drifted")
    _write(path, migration.replace(anchor, guard_sql + anchor, 1))


def add_database_immutability_regressions() -> None:
    """Prove privileged SQL cannot rewrite or extend finalized posted facts."""
    path = "tests/test_postgres_posting.py"
    tests = _read(path)
    if "test_database_rejects_finalized_journal_line_append" in tests:
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

    def test_database_rejects_finalized_journal_line_append(self) -> None:
        """Balanced extra lines cannot be appended after the posting receipt finalizes a journal."""
        receipt = self.ledger.post(self._two_line_proposal(), self.policy)
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            journal_id, chart_account_id = connection.execute(
                """
                SELECT general_journal.general_journal_id,
                       journal_entry_line.chart_account_id
                  FROM accounting_core.general_journal
                  JOIN accounting_core.journal_entry_line
                    ON journal_entry_line.tenant_account_id = general_journal.tenant_account_id
                   AND journal_entry_line.general_journal_id = general_journal.general_journal_id
                 WHERE general_journal.tenant_account_id = %s
                   AND general_journal.journal_reference = %s
                   AND journal_entry_line.line_number = 1
                """,
                (self.tenant_id, receipt.journal_reference),
            ).fetchone()
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "journal_finalized",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.journal_entry_line (
                        tenant_account_id, general_journal_id, line_number, chart_account_id,
                        account_role_code, debit_amount, credit_amount, line_description
                    )
                    VALUES (%s, %s, 3, %s, 'accounts_receivable', 1, 0, 'late append')
                    """,
                    (self.tenant_id, journal_id, chart_account_id),
                )
            connection.rollback()
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 2)

    def test_database_rejects_finalized_proposal_hash_rewrite(self) -> None:
        """A posted command's immutable source hash cannot be rewritten after receipt issuance."""
        proposal = self._two_line_proposal()
        self.ledger.post(proposal, self.policy)
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "journal_finalized",
            ):
                connection.execute(
                    """
                    UPDATE accounting_integration.journal_proposal_record
                       SET source_payload_hash = %s
                     WHERE tenant_account_id = %s
                       AND idempotency_key = %s
                    """,
                    (
                        "sha256:" + "f" * 64,
                        self.tenant_id,
                        proposal.idempotency_key,
                    ),
                )
            connection.rollback()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            stored_hash = connection.execute(
                """
                SELECT source_payload_hash
                  FROM accounting_integration.journal_proposal_record
                 WHERE tenant_account_id = %s
                   AND idempotency_key = %s
                """,
                (self.tenant_id, proposal.idempotency_key),
            ).fetchone()[0]
        self.assertEqual(stored_hash, proposal.source_payload_hash)

'''
    if marker not in tests:
        raise SystemExit("PostgreSQL immutability test insertion marker drifted")
    _write(path, tests.replace(marker, regressions + marker, 1))


def update_immutability_documentation() -> None:
    """Describe PostgreSQL as the final append-only enforcement boundary."""
    adr_path = "docs/adr/0003-append-only-journals.md"
    adr = _read(adr_path)
    old = """Checked-in PostgreSQL migrations cannot `UPDATE`, `DELETE`, `TRUNCATE`, or `DROP TABLE` `general_journal` or `journal_entry_line`, including schema-qualified and quoted identifiers. `scripts/validate_repository.py` rejects those statements so later schema work cannot rewrite posted journals and still pass CI. Destructive statements against unrelated tables remain valid.\n"""
    new = """Checked-in PostgreSQL migrations cannot `UPDATE`, `DELETE`, `TRUNCATE`, or `DROP TABLE` `general_journal` or `journal_entry_line`, including schema-qualified and quoted identifiers. `scripts/validate_repository.py` rejects those statements so later schema work cannot rewrite posted journals and still pass CI. Destructive statements against unrelated tables remain valid. PostgreSQL itself is also fail-closed: `reject_posted_ledger_mutation` guards posted journal headers, lines, source references, reversal links, and posting receipts against direct `UPDATE` or `DELETE`; command evidence becomes immutable once its receipt exists. Receipt issuance also finalizes the journal population, so later `INSERT` operations cannot append lines, source references, or reversal lineage to an already-posted journal. Corrections therefore remain append-only reversal/reposting operations rather than mutation or extension of an existing posting.\n"""
    if old not in adr:
        raise SystemExit("ADR 0003 append-only database paragraph drifted")
    _write(adr_path, adr.replace(old, new, 1))

    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path)
    anchor = """PostgreSQL deferred constraint triggers verify the complete journal population when a transaction commits. Every durable `general_journal` must contain lines with exactly equal debit and credit totals. A direct-SQL mutation that leaves a journal empty or unbalanced fails with `journal_unbalanced`; repair the transaction before retrying rather than disabling the trigger.\n"""
    replacement = anchor + "\nDirect `UPDATE` or `DELETE` against posted journal headers, lines, source references, reversal links, posting receipts, or finalized command evidence fails closed. A posting receipt also seals the journal population: later line, source-reference, or reversal-link inserts fail with `journal_finalized`. Correct accounting facts by posting an explicit reversal and replacement; never disable the immutability/finalization triggers as an operational repair.\n"
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
