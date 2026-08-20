"""Repair cross-book chart-account integrity and stale buyer-facing README claims."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return UTF-8 repository text."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace UTF-8 repository text."""
    Path(path).write_text(text, encoding="utf-8")


def bind_role_mappings_to_accounting_books() -> None:
    """Make role mappings reference a chart account in the same accounting book."""
    path = "database/migrations/0001_accounting_foundation.sql"
    text = _read(path)
    chart_unique = "    UNIQUE (tenant_account_id, accounting_book_id, chart_account_id),\n"
    if chart_unique not in text:
        anchor = "    UNIQUE (tenant_account_id, chart_account_id),\n    CHECK (valid_to IS NULL OR valid_to > valid_from)\n);\n\nCREATE TABLE accounting_core.account_role_mapping"
        replacement = (
            "    UNIQUE (tenant_account_id, chart_account_id),\n"
            + chart_unique
            + "    CHECK (valid_to IS NULL OR valid_to > valid_from)\n);\n\n"
            "CREATE TABLE accounting_core.account_role_mapping"
        )
        if anchor not in text:
            raise SystemExit("chart-account composite identity anchor drifted")
        text = text.replace(anchor, replacement, 1)

    role_start = text.index("CREATE TABLE accounting_core.account_role_mapping")
    role_end = text.index("CREATE TABLE accounting_core.fiscal_calendar", role_start)
    role_block = text[role_start:role_end]
    old_fk = '''    FOREIGN KEY (tenant_account_id, chart_account_id)
        REFERENCES accounting_core.chart_account (tenant_account_id, chart_account_id),
'''
    new_fk = '''    FOREIGN KEY (tenant_account_id, accounting_book_id, chart_account_id)
        REFERENCES accounting_core.chart_account (
            tenant_account_id, accounting_book_id, chart_account_id
        ),
'''
    if new_fk not in role_block:
        if old_fk not in role_block:
            raise SystemExit("account-role chart-account foreign-key anchor drifted")
        role_block = role_block.replace(old_fk, new_fk, 1)
        text = text[:role_start] + role_block + text[role_end:]
    _write(path, text)


def enforce_line_book_binding_at_database_boundary() -> None:
    """Reject a journal line whose chart account belongs to another accounting book."""
    path = "database/migrations/0005_closed_period_guard.sql"
    text = _read(path)
    if "journal_line_book_binding_guard" in text:
        return
    anchor = "\nCOMMIT;\n"
    guard = r'''

CREATE OR REPLACE FUNCTION accounting_core.assert_journal_line_book_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    journal_book_id uuid;
    chart_book_id uuid;
BEGIN
    SELECT general_journal.accounting_book_id
      INTO journal_book_id
      FROM accounting_core.general_journal
     WHERE general_journal.tenant_account_id = NEW.tenant_account_id
       AND general_journal.general_journal_id = NEW.general_journal_id;

    SELECT chart_account.accounting_book_id
      INTO chart_book_id
      FROM accounting_core.chart_account
     WHERE chart_account.tenant_account_id = NEW.tenant_account_id
       AND chart_account.chart_account_id = NEW.chart_account_id;

    IF journal_book_id IS NULL OR chart_book_id IS NULL OR journal_book_id <> chart_book_id THEN
        RAISE EXCEPTION
            'journal line chart account must belong to the journal accounting book (chart_account_book_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER journal_line_book_binding_guard
    BEFORE INSERT OR UPDATE OF general_journal_id, chart_account_id
    ON accounting_core.journal_entry_line
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assert_journal_line_book_binding();
'''
    if text.count(anchor) != 1:
        raise SystemExit("closed-period migration COMMIT anchor drifted")
    _write(path, text.replace(anchor, guard + anchor, 1))


def add_cross_book_regressions() -> None:
    """Prove mappings and direct journal lines cannot cross accounting-book boundaries."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)
    if "test_database_rejects_cross_book_chart_account_line" in text:
        return
    marker = "    def _seed_master_data(self, *, period_status_code: str) -> str:\n"
    tests = '''    def test_database_rejects_cross_book_chart_account_line(self) -> None:
        """A direct SQL journal line cannot bind a chart account from another book."""
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            legal_entity_id, primary_book_id, period_id = connection.execute(
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
                 LIMIT 1
                """,
                (self.tenant_id,),
            ).fetchone()
            other_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id, legal_entity_id, book_role_code, book_name,
                    reporting_currency_code, valid_from
                )
                VALUES (%s, %s, 'secondary', 'Secondary ledger', 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (self.tenant_id, legal_entity_id, datetime(2026, 1, 1, tzinfo=timezone.utc)),
            ).fetchone()[0]
            other_account_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id, accounting_book_id, chart_account_code,
                    account_name, normal_balance_code, valid_from
                )
                VALUES (%s, %s, '119999', 'Secondary clearing', 'debit', %s)
                RETURNING chart_account_id
                """,
                (self.tenant_id, other_book_id, datetime(2026, 1, 1, tzinfo=timezone.utc)),
            ).fetchone()[0]
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
                    f"cross-book:{uuid.uuid4()}",
                    "sha256:" + "e" * 64,
                ),
            ).fetchone()[0]
            journal_id = connection.execute(
                """
                INSERT INTO accounting_core.general_journal (
                    tenant_account_id, legal_entity_id, accounting_book_id, fiscal_period_id,
                    journal_reference, journal_status_code, transaction_currency_code,
                    functional_currency_code, transaction_date, accounting_date,
                    source_proposal_record_id, accounting_policy_version, posting_rule_version
                )
                VALUES (%s, %s, %s, %s, %s, 'posted', 'KRW', 'KRW', %s, %s, %s, 'ifrs-v1', 'billing-issued-v1')
                RETURNING general_journal_id
                """,
                (
                    self.tenant_id,
                    legal_entity_id,
                    primary_book_id,
                    period_id,
                    f"urn:cwl:accounting:general_journal:cross-book:{uuid.uuid4()}",
                    date(2026, 8, 31),
                    date(2026, 8, 31),
                    proposal_record_id,
                ),
            ).fetchone()[0]
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "chart_account_book_mismatch",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.journal_entry_line (
                        tenant_account_id, general_journal_id, line_number, chart_account_id,
                        account_role_code, debit_amount, credit_amount
                    )
                    VALUES (%s, %s, 1, %s, 'accounts_receivable', 1, 0)
                    """,
                    (self.tenant_id, journal_id, other_account_id),
                )
            connection.rollback()

    def test_database_rejects_cross_book_role_mapping(self) -> None:
        """A role mapping cannot reference a chart account from another book."""
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            legal_entity_id, primary_book_id = connection.execute(
                """
                SELECT legal_entity_record.legal_entity_id, accounting_book.accounting_book_id
                  FROM accounting_core.legal_entity_record
                  JOIN accounting_core.accounting_book
                    ON accounting_book.tenant_account_id = legal_entity_record.tenant_account_id
                   AND accounting_book.legal_entity_id = legal_entity_record.legal_entity_id
                 WHERE legal_entity_record.tenant_account_id = %s
                 LIMIT 1
                """,
                (self.tenant_id,),
            ).fetchone()
            other_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id, legal_entity_id, book_role_code, book_name,
                    reporting_currency_code, valid_from
                )
                VALUES (%s, %s, 'mapping_test', 'Mapping test ledger', 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (self.tenant_id, legal_entity_id, datetime(2026, 1, 1, tzinfo=timezone.utc)),
            ).fetchone()[0]
            other_account_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id, accounting_book_id, chart_account_code,
                    account_name, normal_balance_code, valid_from
                )
                VALUES (%s, %s, '129999', 'Mapping test account', 'debit', %s)
                RETURNING chart_account_id
                """,
                (self.tenant_id, other_book_id, datetime(2026, 1, 1, tzinfo=timezone.utc)),
            ).fetchone()[0]
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_core.account_role_mapping (
                        tenant_account_id, accounting_book_id, account_role_code,
                        chart_account_id, accounting_policy_version, posting_rule_version,
                        valid_from
                    )
                    VALUES (%s, %s, 'cross_book_test', %s, 'ifrs-v1', 'billing-issued-v1', %s)
                    """,
                    (
                        self.tenant_id,
                        primary_book_id,
                        other_account_id,
                        datetime(2026, 1, 1, tzinfo=timezone.utc),
                    ),
                )
            connection.rollback()

'''
    if marker not in text:
        raise SystemExit("PostgreSQL regression insertion marker drifted")
    _write(path, text.replace(marker, tests + marker, 1))


def refresh_readme_runtime_truth() -> None:
    """Describe the implemented HTTP/reporting surface without implying auto-start."""
    path = "README.md"
    text = _read(path)
    text = text.replace(
        "This tree is the first executable foundation (Python package\n`accounting-information-platform` 0.1.0, Development Status: Pre-Alpha). It is\nnot a hosted service and does not start an HTTP listener.\n",
        "This tree is an executable foundation (Python package\n`accounting-information-platform` 0.1.0, Development Status: Pre-Alpha). It does\nnot auto-start a listener on import or installation; the repository now includes\nan explicit tenant-bound HTTP server factory for the implemented accounting\ncommands and reporting reads.\n",
    )
    old_gap = (
        "What is not present: an HTTP or event API, foreign exchange, revenue\n"
        "schedules, bank ingestion, financial-statement production, consolidation, or\n"
        "tax calculation. The in-memory `PostingLedger` remains the reference oracle\n"
        "that the PostgreSQL adapter must match.\n"
    )
    new_gap = (
        "What is not present: foreign-exchange accounting, bank-statement ingestion and\n"
        "reconciliation, consolidation/intercompany elimination, automated statutory\n"
        "filing transport, or a production deployment control plane. The in-memory\n"
        "`PostingLedger` remains the reference oracle for posting/reversal behavior that\n"
        "the PostgreSQL adapter must match.\n"
    )
    if old_gap in text:
        text = text.replace(old_gap, new_gap, 1)
    old_call = (
        "No HTTP, gRPC, or live event endpoint is published in this foundation. When a\n"
        "sibling—including Naruon as composition hub—integrates, it uses the versioned\n"
        "file contracts in `schemas/` rather than a private table or undeclared payload.\n"
    )
    new_call = (
        "The repository publishes both versioned file contracts and a bounded HTTP\n"
        "adapter. The HTTP process must be started explicitly by its host; importing the\n"
        "package does not open a port. Siblings may submit journal proposals and\n"
        "reversals, query tenant-scoped catalogs/ledger/reporting projections, and invoke\n"
        "period-close or Billing-pull commands through that adapter without direct SQL.\n"
        "The contracts in `schemas/` remain the authority boundary between products.\n"
    )
    if old_call in text:
        text = text.replace(old_call, new_call, 1)
    text = text.replace(
        "Until an HTTP or CloudEvents adapter exists, the in-process calls are\n",
        "The in-process reference calls remain available alongside the HTTP adapter:\n",
        1,
    )
    if "No HTTP, gRPC, or live event endpoint is published" in text:
        raise SystemExit("stale README HTTP claim remains")
    if "What is not present: an HTTP or event API" in text:
        raise SystemExit("stale README capability gap remains")
    _write(path, text)


def update_docs() -> None:
    """Record the book-binding invariant in architecture and changelog."""
    architecture_path = "docs/ARCHITECTURE.md"
    architecture = _read(architecture_path)
    entry = (
        "\nChart accounts are book-scoped facts. Role mappings use a composite tenant/book/account foreign key, "
        "and PostgreSQL rejects a journal line whose referenced chart account belongs to a different accounting book.\n"
    )
    if "journal line whose referenced chart account belongs to a different accounting book" not in architecture:
        architecture = architecture.rstrip() + entry
        _write(architecture_path, architecture.rstrip() + "\n")

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    line = (
        "- Bound chart-account usage to the owning accounting book in database constraints and direct-SQL journal-line validation.\n"
    )
    if line not in changelog:
        marker = "### Changed\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG Changed anchor drifted")
        changelog = changelog.replace(marker, marker + "\n" + line, 1)
        _write(changelog_path, changelog)


def main() -> None:
    """Apply cross-book integrity and README truth repairs."""
    bind_role_mappings_to_accounting_books()
    enforce_line_book_binding_at_database_boundary()
    add_cross_book_regressions()
    refresh_readme_runtime_truth()
    update_docs()


if __name__ == "__main__":
    main()
