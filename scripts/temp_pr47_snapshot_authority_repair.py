#!/usr/bin/env python3
"""Add database-owned reconciliation run snapshot authority to stacked PR #47."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_reconciliation_exception_resolution_command.sql"
LIFECYCLE = ROOT / "src/accounting_information_platform/reconciliation_lifecycle.py"
LIFECYCLE_TEST = ROOT / "tests/test_reconciliation_lifecycle_postgres.py"
ADR = ROOT / "docs/adr/0062-reconciliation-exception-resolution-command-authority.md"
TRACEABILITY = ROOT / "docs/doctoring/STANDARD_TRACEABILITY.md"
WORKFLOW = ROOT / ".github/workflows/_temp_pr47_review_repair.yml"
SELF = Path(__file__).resolve()


def once(text: str, old: str, new: str, label: str) -> str:
    """Replace exactly one guarded fragment."""
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_database_snapshot_authority() -> None:
    """Derive the lifecycle state digest inside PostgreSQL and verify submitted values."""
    text = MIGRATION.read_text(encoding="utf-8")
    anchor = """-- Replace the interim all-exception rejection from migration 0019. A run may
-- now finalize only when every exception is terminal under exactly one durable
-- command whose target status matches the exception row. The transition hash
-- remains database-owned and all other reviewed-match controls are unchanged.
CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_run_transition_hash()
"""
    function = r'''-- The lifecycle snapshot is database-owned rather than a caller assertion.  It
-- binds the immutable run-opening command and the complete authority-relevant raw
-- populations from which the application derives its exact book-to-bank bridge.
-- Stable ordering plus jsonb canonical rendering makes the digest reproducible
-- inside PostgreSQL without trusting application serialization.
CREATE OR REPLACE FUNCTION accounting_core.reconciliation_run_transition_snapshot_hash(
    snapshot_tenant_account_id uuid,
    snapshot_reconciliation_run_id uuid
)
RETURNS text
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    canonical_snapshot jsonb;
BEGIN
    SELECT jsonb_build_object(
        'tenant_reference', tenant.tenant_account_code,
        'reconciliation_run_id', run.reconciliation_run_id::text,
        'run_status_code', run.run_status_code,
        'currency_code', run.currency_code,
        'bank_account_assignment_id', run.bank_account_assignment_id::text,
        'bank_cutoff_at', to_char(
            run.bank_cutoff_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'book_cutoff_at', to_char(
            run.book_cutoff_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'knowledge_cutoff_at', to_char(
            run.knowledge_cutoff_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'opening_command_hash', opening_command.reconciliation_command_hash,
        'statement_record_id', statement.bank_statement_record_id::text,
        'statement_opening_balance_hash', statement.opening_balance_hash,
        'statement_closing_balance_hash', statement.closing_balance_hash,
        'statement_balances', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_array(
                    balance.source_balance_hash,
                    balance.balance_sequence_number,
                    balance.balance_amount::text,
                    balance.balance_currency_code,
                    balance.credit_debit_code
                )
                ORDER BY balance.balance_sequence_number, balance.bank_statement_balance_id
            )
            FROM accounting_integration.bank_statement_balance AS balance
            WHERE balance.tenant_account_id = run.tenant_account_id
              AND balance.bank_statement_record_id = statement.bank_statement_record_id
              AND balance.recorded_at <= run.knowledge_cutoff_at
        ), '[]'::jsonb),
        'statement_entries', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_array(
                    COALESCE(NULLIF(entry.source_entry_identity, ''), entry.bank_statement_entry_id::text),
                    entry.entry_sequence_number,
                    entry.entry_amount::text,
                    entry.entry_currency_code,
                    entry.credit_debit_code,
                    entry.reversal_indicator,
                    entry.source_entry_hash
                )
                ORDER BY entry.entry_sequence_number, entry.bank_statement_entry_id
            )
            FROM accounting_integration.bank_statement_entry AS entry
            WHERE entry.tenant_account_id = run.tenant_account_id
              AND entry.bank_statement_record_id = statement.bank_statement_record_id
              AND entry.recorded_at <= run.knowledge_cutoff_at
        ), '[]'::jsonb),
        'cash_journal_lines', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_array(
                    journal.journal_reference,
                    journal.accounting_date::text,
                    to_char(
                        journal.posted_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    ),
                    line.line_number,
                    line.debit_amount::text,
                    line.credit_amount::text,
                    journal.transaction_currency_code
                )
                ORDER BY journal.accounting_date, journal.posted_at,
                         journal.journal_reference, line.line_number
            )
            FROM accounting_core.journal_entry_line AS line
            JOIN accounting_core.general_journal AS journal
              ON journal.tenant_account_id = line.tenant_account_id
             AND journal.general_journal_id = line.general_journal_id
            JOIN accounting_core.chart_account AS cash_account
              ON cash_account.tenant_account_id = line.tenant_account_id
             AND cash_account.chart_account_id = line.chart_account_id
            WHERE line.tenant_account_id = run.tenant_account_id
              AND line.chart_account_id = assignment.chart_account_id
              AND journal.accounting_book_id = cash_account.accounting_book_id
              AND journal.accounting_date <= run.book_cutoff_at::date
              AND journal.posted_at <= run.knowledge_cutoff_at
        ), '[]'::jsonb),
        'match_state', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_array(
                    reviewed_match.reconciliation_match_id::text,
                    reviewed_match.match_status_code,
                    COALESCE(approval.approval_decision_code, ''),
                    COALESCE(approval.reconciliation_snapshot_hash, '')
                )
                ORDER BY reviewed_match.reconciliation_match_id,
                         approval.reconciliation_approval_id
            )
            FROM accounting_core.reconciliation_match AS reviewed_match
            LEFT JOIN accounting_core.reconciliation_approval AS approval
              ON approval.tenant_account_id = reviewed_match.tenant_account_id
             AND approval.reconciliation_run_id = reviewed_match.reconciliation_run_id
             AND approval.reconciliation_match_id = reviewed_match.reconciliation_match_id
            WHERE reviewed_match.tenant_account_id = run.tenant_account_id
              AND reviewed_match.reconciliation_run_id = run.reconciliation_run_id
        ), '[]'::jsonb),
        'statement_allocations', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_array(
                    allocation.reconciliation_allocation_id::text,
                    allocation.reconciliation_match_id::text,
                    allocation.statement_entry_reference,
                    allocation.allocated_amount::text
                )
                ORDER BY allocation.statement_entry_reference,
                         allocation.reconciliation_allocation_id
            )
            FROM accounting_core.statement_match_allocation AS allocation
            WHERE allocation.tenant_account_id = run.tenant_account_id
              AND allocation.reconciliation_run_id = run.reconciliation_run_id
        ), '[]'::jsonb),
        'journal_allocations', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_array(
                    allocation.reconciliation_allocation_id::text,
                    allocation.reconciliation_match_id::text,
                    allocation.journal_reference,
                    allocation.allocated_amount::text
                )
                ORDER BY allocation.journal_reference,
                         allocation.reconciliation_allocation_id
            )
            FROM accounting_core.journal_match_allocation AS allocation
            WHERE allocation.tenant_account_id = run.tenant_account_id
              AND allocation.reconciliation_run_id = run.reconciliation_run_id
        ), '[]'::jsonb),
        'exception_state', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_array(
                    exception.reconciliation_exception_id::text,
                    exception.exception_code,
                    exception.owner_reference,
                    exception.resolution_status_code,
                    to_char(
                        exception.effective_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    ),
                    resolution.reconciliation_exception_resolution_command_id::text,
                    resolution.target_resolution_status_code,
                    resolution.resolution_evidence_reference,
                    resolution.resolution_evidence_hash,
                    resolution.reconciliation_exception_resolution_command_hash
                )
                ORDER BY exception.reconciliation_exception_id
            )
            FROM accounting_core.reconciliation_exception AS exception
            LEFT JOIN accounting_core.reconciliation_exception_resolution_command AS resolution
              ON resolution.tenant_account_id = exception.tenant_account_id
             AND resolution.reconciliation_run_id = exception.reconciliation_run_id
             AND resolution.reconciliation_exception_id = exception.reconciliation_exception_id
            WHERE exception.tenant_account_id = run.tenant_account_id
              AND exception.reconciliation_run_id = run.reconciliation_run_id
        ), '[]'::jsonb)
    )
    INTO canonical_snapshot
    FROM accounting_core.reconciliation_run AS run
    JOIN accounting_core.reconciliation_run_command AS opening_command
      ON opening_command.tenant_account_id = run.tenant_account_id
     AND opening_command.reconciliation_run_id = run.reconciliation_run_id
    JOIN accounting_core.tenant_account AS tenant
      ON tenant.tenant_account_id = run.tenant_account_id
    JOIN accounting_integration.bank_statement_record AS statement
      ON statement.tenant_account_id = opening_command.tenant_account_id
     AND statement.bank_statement_record_id = opening_command.bank_statement_record_id
    JOIN accounting_core.bank_account_assignment AS assignment
      ON assignment.tenant_account_id = run.tenant_account_id
     AND assignment.bank_account_assignment_id = run.bank_account_assignment_id
    WHERE run.tenant_account_id = snapshot_tenant_account_id
      AND run.reconciliation_run_id = snapshot_reconciliation_run_id;

    IF canonical_snapshot IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle database snapshot scope cannot be canonicalized (reconciliation_lifecycle_snapshot_scope)'
            USING ERRCODE = '23514';
    END IF;

    RETURN 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_run_transition_database_snapshot:v1|'
                || canonical_snapshot::text,
                'UTF8'
            )
        ),
        'hex'
    );
END;
$$;

-- Replace the interim all-exception rejection from migration 0019. A run may
-- now finalize only when every exception is terminal under exactly one durable
-- command whose target status matches the exception row. The submitted state
-- digest is independently re-derived from PostgreSQL-owned source/review facts
-- under the same lifecycle lock before it can enter the command hash.
CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_run_transition_hash()
'''
    text = once(text, anchor, function, "snapshot function insertion")
    text = once(
        text,
        "    current_status text;\n    canonical_command jsonb;",
        "    current_status text;\n    expected_snapshot_hash text;\n    canonical_command jsonb;",
        "snapshot declaration",
    )
    review_anchor = """    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_match AS reviewed_match
        LEFT JOIN accounting_core.reconciliation_approval AS approval
          ON approval.tenant_account_id = reviewed_match.tenant_account_id
         AND approval.reconciliation_run_id = reviewed_match.reconciliation_run_id
         AND approval.reconciliation_match_id = reviewed_match.reconciliation_match_id
        WHERE reviewed_match.tenant_account_id = NEW.tenant_account_id
          AND reviewed_match.reconciliation_run_id = NEW.reconciliation_run_id
          AND reviewed_match.match_status_code IN ('approved', 'rejected')
          AND (
              approval.reconciliation_approval_id IS NULL
              OR approval.approval_decision_code IS DISTINCT FROM reviewed_match.match_status_code
              OR approval.reconciliation_snapshot_hash IS DISTINCT FROM
                 accounting_core.reconciliation_match_snapshot_hash(
                     reviewed_match.tenant_account_id,
                     reviewed_match.reconciliation_run_id,
                     reviewed_match.reconciliation_match_id
                 )
          )
    ) THEN
        RAISE EXCEPTION
            'reconciliation reviewed match lacks current decision-consistent approval evidence (reconciliation_lifecycle_review)'
            USING ERRCODE = '23514';
    END IF;

"""
    verification = review_anchor + """    expected_snapshot_hash :=
        accounting_core.reconciliation_run_transition_snapshot_hash(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id
        );
    IF NEW.reconciliation_snapshot_hash IS DISTINCT FROM expected_snapshot_hash THEN
        RAISE EXCEPTION
            'reconciliation lifecycle snapshot does not match current database-owned run evidence (reconciliation_lifecycle_snapshot_mismatch)'
            USING ERRCODE = '23514';
    END IF;

"""
    text = once(text, review_anchor, verification, "snapshot verification")
    MIGRATION.write_text(text, encoding="utf-8")


def patch_application_authority() -> None:
    """Make production obtain the transition state digest from the same PostgreSQL function."""
    text = LIFECYCLE.read_text(encoding="utf-8")
    old = '''        snapshot_hash = _transition_snapshot_hash(
            run_id,
            str(opening_command[0]),
            bridge,
            match_state,
            exception_state,
            currency_code=authoritative_currency_code,
            exception_resolution_state=exception_resolution_state,
        )
'''
    new = '''        snapshot_hash_row = connection.execute(
            """
            SELECT accounting_core.reconciliation_run_transition_snapshot_hash(%s, %s)
            """,
            (tenant_id, run_id),
        ).fetchone()
        if snapshot_hash_row is None or not snapshot_hash_row[0]:
            raise AccountingValidationError(
                "reconciliation lifecycle database snapshot evidence is missing. Restore the "
                "authoritative run evidence and migration, then retry."
            )
        snapshot_hash = str(snapshot_hash_row[0])
'''
    text = once(text, old, new, "application database snapshot call")
    old_doc = '''def _transition_snapshot_hash(
'''
    if old_doc in text and "Legacy deterministic fixture helper" not in text:
        text = text.replace(
            '    """Bind run scope, populations, bridge arithmetic, and review authority to one digest."""',
            '    """Legacy deterministic fixture helper; production authority is PostgreSQL-derived."""',
            1,
        )
    LIFECYCLE.write_text(text, encoding="utf-8")


def patch_lifecycle_tests() -> None:
    """Prove stale/caller-supplied snapshot values fail and current DB state changes the digest."""
    text = LIFECYCLE_TEST.read_text(encoding="utf-8")
    text = once(
        text,
        "    def _insert_transition_only(self, connection: psycopg.Connection) -> None:\n        \"\"\"Insert a syntactically valid command without its required paired status update.\"\"\"\n        connection.execute(",
        "    def _insert_transition_only(\n        self,\n        connection: psycopg.Connection,\n        *,\n        snapshot_hash: str | None = None,\n    ) -> None:\n        \"\"\"Insert a command without status, using fresh database snapshot evidence by default.\"\"\"\n        if snapshot_hash is None:\n            snapshot_hash = str(\n                connection.execute(\n                    \"SELECT accounting_core.reconciliation_run_transition_snapshot_hash(%s, %s)\",\n                    (self._tenant_id(connection), self.opened[\"reconciliation_run_id\"]),\n                ).fetchone()[0]\n            )\n        connection.execute(",
        "transition test helper signature",
    )
    text = once(
        text,
        '                "sha256:" + "d" * 64,\n                "sha256:" + "1" * 64,',
        '                snapshot_hash,\n                "sha256:" + "1" * 64,',
        "transition helper snapshot parameter",
    )
    anchor = '''    def test_transition_command_cannot_commit_without_reconciled_status(self) -> None:
        """A lifecycle command cannot be parked for a later raw status rewrite."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            self._insert_transition_only(connection)
            with self.assertRaisesRegex(psycopg.Error, "commit atomically"):
                connection.commit()
            connection.rollback()

'''
    addition = anchor + '''    def test_database_rejects_caller_supplied_transition_snapshot_hash(self) -> None:
        """A syntactically valid digest cannot substitute for current PostgreSQL run state."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(psycopg.Error, "snapshot does not match"):
                self._insert_transition_only(
                    connection,
                    snapshot_hash="sha256:" + "d" * 64,
                )
            connection.rollback()

    def test_database_snapshot_digest_changes_with_review_state(self) -> None:
        """The database digest is derived from current authority-bearing exception state."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            run_id = self.opened["reconciliation_run_id"]
            before = connection.execute(
                "SELECT accounting_core.reconciliation_run_transition_snapshot_hash(%s, %s)",
                (tenant_id, run_id),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception (
                    tenant_account_id,
                    reconciliation_run_id,
                    exception_code,
                    owner_reference,
                    next_action,
                    effective_at,
                    resolution_status_code
                )
                VALUES (%s, %s, 'snapshot_state_test',
                        'urn:cwl:principal:test_controller',
                        'Review this fixture exception.', %s, 'open')
                """,
                (
                    tenant_id,
                    run_id,
                    datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc),
                ),
            )
            after = connection.execute(
                "SELECT accounting_core.reconciliation_run_transition_snapshot_hash(%s, %s)",
                (tenant_id, run_id),
            ).fetchone()[0]
            self.assertNotEqual(before, after)
            connection.rollback()

'''
    text = once(text, anchor, addition, "snapshot tests insertion")
    LIFECYCLE_TEST.write_text(text, encoding="utf-8")


def patch_docs() -> None:
    """Record database-owned lifecycle snapshot and deferred outbox evidence accurately."""
    text = ADR.read_text(encoding="utf-8")
    needle = "The finalization command binds exact statement/book population identities"
    if needle in text and "database-owned lifecycle snapshot" not in text:
        text = text.replace(
            needle,
            "The finalization command binds a database-owned lifecycle snapshot over the current run scope, opening-command provenance, immutable statement/cash-journal populations, match/approval state, allocations, and exception-resolution state. PostgreSQL re-derives that snapshot under the lifecycle lock and rejects a submitted digest that differs. It also binds exact statement/book population identities",
            1,
        )
    elif "database-owned lifecycle snapshot" not in text:
        text += "\n\nPostgreSQL independently derives the reconciliation-run lifecycle snapshot from current run/opening-command, statement, cash-journal, allocation, reviewed-match/approval, and exception-resolution rows under the lifecycle lock. Production reads that same database function before insert, and the transition trigger recomputes and rejects any mismatching submitted digest; a caller-formatted SHA-256 is never authority.\n"
    ADR.write_text(text, encoding="utf-8")

    text = TRACEABILITY.read_text(encoding="utf-8")
    old = "requires reviewer separation and temporal causality, and commits command/status/outbox evidence atomically."
    new = "requires reviewer separation and temporal causality, and uses a deferred PostgreSQL guard to require the matching command/status/outbox triple at commit."
    if old in text:
        text = text.replace(old, new, 1)
    snapshot_phrase = "Concurrent exact retries use fresh transactions after PostgreSQL serialization failure;"
    if snapshot_phrase in text and "database-owned run snapshot" not in text:
        text = text.replace(
            snapshot_phrase,
            "Run finalization also uses a database-owned run snapshot over current opening-command, statement, cash-journal, allocation, reviewed-match/approval, and exception-resolution facts; PostgreSQL independently re-derives and verifies the submitted transition digest before hashing the command. " + snapshot_phrase,
            1,
        )
    TRACEABILITY.write_text(text, encoding="utf-8")


def remove_self() -> None:
    """Remove this temporary repair helper; the workflow removes itself in the first helper."""
    SELF.unlink()


def main() -> int:
    """Apply database-owned lifecycle snapshot authority and its acceptance evidence."""
    patch_database_snapshot_authority()
    patch_application_authority()
    patch_lifecycle_tests()
    patch_docs()
    remove_self()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
