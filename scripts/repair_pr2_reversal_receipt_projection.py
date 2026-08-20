"""One-shot repair aligning reversal receipts with the published receipt contract."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def normalize_reference_reversal_receipts() -> None:
    """Return `reversed` receipts from both fresh and reconstructed reversals."""
    path = "src/accounting_information_platform/core.py"
    text = _read(path)

    reverse_start = text.index("    def reverse(\n")
    reverse_end = text.index("    def trial_balance(\n", reverse_start)
    reverse_block = text[reverse_start:reverse_end]
    if 'posting_status_code="reversed"' not in reverse_block:
        old = '            posting_status_code="posted",\n'
        if old not in reverse_block:
            raise SystemExit("reference reversal receipt status anchor drifted")
        reverse_block = reverse_block.replace(
            old, '            posting_status_code="reversed",\n', 1
        )
        text = text[:reverse_start] + reverse_block + text[reverse_end:]

    receipt_start = text.index("    def _receipt_for_posted_journal(")
    receipt_end = text.index("    @staticmethod\n    def _resolve_line(", receipt_start)
    receipt_block = text[receipt_start:receipt_end]
    if 'posting_status_code="reversed"' not in receipt_block:
        old = '            posting_status_code="posted",\n'
        if old not in receipt_block:
            raise SystemExit("reference reconstructed reversal receipt anchor drifted")
        receipt_block = receipt_block.replace(
            old, '            posting_status_code="reversed",\n', 1
        )
        text = text[:receipt_start] + receipt_block + text[receipt_end:]

    _write(path, text)


def normalize_postgres_reversal_receipts() -> None:
    """Persist and project reversal receipts as `reversed` with original-journal evidence."""
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)

    reverse_start = text.index("    def reverse(\n")
    reverse_end = text.index("    def load_reversal_policy(\n", reverse_start)
    reverse_block = text[reverse_start:reverse_end]
    if 'posting_status_code="reversed"' not in reverse_block:
        old = '            posting_status_code="posted",\n'
        if old not in reverse_block:
            raise SystemExit("PostgreSQL reversal receipt status anchor drifted")
        reverse_block = reverse_block.replace(
            old, '            posting_status_code="reversed",\n', 1
        )
        text = text[:reverse_start] + reverse_block + text[reverse_end:]

    receipt_start = text.index("    def _receipt_for_journal(")
    receipt_end = text.index("    def _book_name_for_proposal(", receipt_start)
    receipt_block = text[receipt_start:receipt_end]
    if 'posting_status_code="reversed"' not in receipt_block:
        old = '            posting_status_code="posted",\n'
        if old not in receipt_block:
            raise SystemExit("PostgreSQL reconstructed reversal receipt anchor drifted")
        receipt_block = receipt_block.replace(
            old, '            posting_status_code="reversed",\n', 1
        )
        text = text[:receipt_start] + receipt_block + text[receipt_end:]

    loader_start = text.index("    def _load_published_receipt(")
    loader_end = text.index("    def _load_lines(\n", loader_start)
    replacement = '''    def _load_published_receipt(
        self, connection: object, tenant_id: UUID, idempotency_key: str
    ) -> dict[str, object]:
        """Return one posted/reversed receipt with state-specific journal lineage."""
        row = connection.execute(
            """
            SELECT posting_receipt.posting_receipt_id,
                   posting_receipt.created_at,
                   posting_receipt.receipt_status_code,
                   general_journal.journal_reference,
                   general_journal.transaction_currency_code,
                   general_journal.functional_currency_code,
                   general_journal.accounting_policy_version,
                   general_journal.posting_rule_version,
                   accounting_book.book_name,
                   legal_entity_record.legal_entity_code,
                   fiscal_period.period_code,
                   (
                       SELECT COUNT(*)
                       FROM accounting_core.journal_entry_line
                       WHERE tenant_account_id = general_journal.tenant_account_id
                         AND general_journal_id = general_journal.general_journal_id
                   ),
                   journal_proposal_record.idempotency_key,
                   journal_proposal_record.external_proposal_id,
                   journal_proposal_record.source_payload_hash,
                   original_journal.journal_reference
            FROM accounting_integration.posting_receipt
            JOIN accounting_integration.journal_proposal_record
              ON journal_proposal_record.tenant_account_id = posting_receipt.tenant_account_id
             AND journal_proposal_record.proposal_record_id = posting_receipt.proposal_record_id
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = posting_receipt.tenant_account_id
             AND general_journal.general_journal_id = posting_receipt.general_journal_id
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = general_journal.tenant_account_id
             AND accounting_book.accounting_book_id = general_journal.accounting_book_id
            JOIN accounting_core.legal_entity_record
              ON legal_entity_record.tenant_account_id = general_journal.tenant_account_id
             AND legal_entity_record.legal_entity_id = general_journal.legal_entity_id
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id = general_journal.tenant_account_id
             AND fiscal_period.fiscal_period_id = general_journal.fiscal_period_id
            LEFT JOIN accounting_core.journal_reversal
              ON journal_reversal.tenant_account_id = general_journal.tenant_account_id
             AND journal_reversal.reversal_journal_id = general_journal.general_journal_id
            LEFT JOIN accounting_core.general_journal AS original_journal
              ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
             AND original_journal.general_journal_id = journal_reversal.original_journal_id
            WHERE posting_receipt.tenant_account_id = %s
              AND journal_proposal_record.idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                "posting receipt is missing for this idempotency key. "
                "Accept the proposal, then retry the receipt read."
            )
        recorded_at = _format_timestamp(row[1])
        document: dict[str, object] = {
            "receipt_id": str(row[0]),
            "receipt_contract_version": 1,
            "idempotency_key": row[12],
            "source_proposal_id": str(row[13]),
            "source_payload_hash": row[14],
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": row[9],
            "accounting_book_reference": row[8],
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{row[10]}",
            "journal_reference": row[3],
            "accounting_policy_version": row[6],
            "posting_rule_version": row[7],
            "posting_status_code": row[2],
            "recorded_at": recorded_at,
            "posted_at": recorded_at,
            "line_count": int(row[11]),
            "transaction_currency": row[4],
            "functional_currency": row[5],
        }
        if row[15] is not None:
            document["reversal_of_journal_reference"] = row[15]
        return document

'''
    current_loader = text[loader_start:loader_end]
    if 'original_journal.journal_reference' not in current_loader:
        text = text[:loader_start] + replacement + text[loader_end:]
    elif 'document["reversal_of_journal_reference"]' not in current_loader:
        raise SystemExit("published receipt reversal projection partially drifted")

    _write(path, text)


def enforce_database_reversal_receipt_lineage() -> None:
    """Reject posted/reversed receipt states that disagree with reversal lineage."""
    path = "database/migrations/0001_accounting_foundation.sql"
    text = _read(path)
    if "posting_receipt_state_guard" in text:
        return
    anchor = "\nCREATE TABLE accounting_reporting.trial_balance_snapshot (\n"
    guard = r'''

CREATE OR REPLACE FUNCTION accounting_integration.validate_posting_receipt_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    reversal_journal_exists boolean;
BEGIN
    IF NEW.general_journal_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM accounting_core.journal_reversal
        WHERE tenant_account_id = NEW.tenant_account_id
          AND reversal_journal_id = NEW.general_journal_id
    )
      INTO reversal_journal_exists;

    IF NEW.receipt_status_code = 'reversed' AND NOT reversal_journal_exists THEN
        RAISE EXCEPTION
            'reversed receipt must reference a reversal journal (reversal_receipt_requires_reversal_journal)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.receipt_status_code = 'posted' AND reversal_journal_exists THEN
        RAISE EXCEPTION
            'reversal journal receipt must use reversed state (reversal_receipt_state_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER posting_receipt_state_guard
    BEFORE INSERT ON accounting_integration.posting_receipt
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.validate_posting_receipt_state();
'''
    if text.count(anchor) != 1:
        raise SystemExit("posting receipt state trigger anchor drifted")
    _write(path, text.replace(anchor, guard + anchor, 1))


def add_reversal_receipt_regressions() -> None:
    """Cover reference, PostgreSQL, HTTP projection, and database lineage behavior."""
    core_path = "tests/test_accounting_core.py"
    core = _read(core_path)
    start = core.index(
        "    def test_reverse_replays_existing_reversal_when_receipt_cache_is_missing("
    )
    end = core.index("    def test_same_proposal_id_posts_independently_per_tenant(", start)
    block = core[start:end]
    assertion = '        self.assertEqual(reversal.posting_status_code, "reversed")\n'
    if assertion not in block:
        marker = "        self.assertEqual(replay, reversal)\n"
        if marker not in block:
            raise SystemExit("reference reversal receipt regression anchor drifted")
        block = block.replace(marker, marker + assertion, 1)
        core = core[:start] + block + core[end:]
        _write(core_path, core)

    pg_path = "tests/test_postgres_posting.py"
    tests = _read(pg_path)
    start = tests.index("    def test_reverse_preserves_original_and_zeroes_trial_balance(")
    end = tests.index("    def test_closed_period_posts_zero_rows(", start)
    block = tests[start:end]
    assertion = '        self.assertEqual(reversal.posting_status_code, "reversed")\n'
    if assertion not in block:
        marker = "        self.assertEqual(reversal, replayed)\n"
        if marker not in block:
            raise SystemExit("PostgreSQL reversal receipt regression anchor drifted")
        block = block.replace(marker, marker + assertion, 1)
        tests = tests[:start] + block + tests[end:]

    old_http = '        self.assertEqual(reversing["posting_status_code"], "posted")\n'
    new_http = '''        self.assertEqual(reversing["posting_status_code"], "reversed")
        self.assertEqual(
            reversing["reversal_of_journal_reference"], posted["journal_reference"]
        )
'''
    if new_http not in tests:
        if old_http not in tests:
            raise SystemExit("HTTP reversal receipt state assertion drifted")
        tests = tests.replace(old_http, new_http, 1)

    if "test_database_reversal_receipt_state_requires_reversal_lineage" not in tests:
        marker = "    def _seed_master_data(self, *, period_status_code: str) -> str:\n"
        regression = '''    def test_database_reversal_receipt_state_requires_reversal_lineage(self) -> None:
        """Receipt status and journal-reversal lineage must agree at the database boundary."""
        original = self.ledger.post(self._two_line_proposal(), self.policy)
        command_key = f"{self.policy.tenant_reference}:reversal:receipt-state:v1"
        reversal = self.ledger.reverse(
            original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key=command_key,
        )
        document = self.ledger.load_published_receipt_by_key(command_key)
        self.assertEqual(reversal.posting_status_code, "reversed")
        self.assertEqual(document["posting_status_code"], "reversed")
        self.assertEqual(
            document["reversal_of_journal_reference"], original.journal_reference
        )

        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            original_journal_id = connection.execute(
                """
                SELECT general_journal_id
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (self.tenant_id, original.journal_reference),
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
                    f"{command_key}:invalid",
                    "sha256:" + "4" * 64,
                ),
            ).fetchone()[0]
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reversal_receipt_requires_reversal_journal",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_integration.posting_receipt (
                        tenant_account_id, proposal_record_id, general_journal_id,
                        receipt_status_code, receipt_payload_hash
                    )
                    VALUES (%s, %s, %s, 'reversed', %s)
                    """,
                    (
                        self.tenant_id,
                        proposal_record_id,
                        original_journal_id,
                        "sha256:" + "5" * 64,
                    ),
                )
            connection.rollback()

'''
        if marker not in tests:
            raise SystemExit("database reversal receipt regression insertion marker drifted")
        tests = tests.replace(marker, regression + marker, 1)

    _write(pg_path, tests)


def update_documentation() -> None:
    """Document the externally observable reversal-receipt state and lineage evidence."""
    adr_path = "docs/adr/0003-append-only-journals.md"
    adr = _read(adr_path)
    sentence = (
        "Reversal command receipts use `posting_status_code = reversed`, include the "
        "original `reversal_of_journal_reference`, and are persisted only after the "
        "reversal lineage row exists; ordinary posting receipts remain `posted`."
    )
    if sentence not in adr:
        marker = "\n## Consequences\n"
        if marker not in adr:
            raise SystemExit("reversal receipt ADR insertion anchor drifted")
        adr = adr.replace(marker, "\n" + sentence + "\n" + marker, 1)
        _write(adr_path, adr)

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    line = (
        "- Aligned reversal receipts with the published `reversed` state, original-journal "
        "lineage projection, and PostgreSQL state/lineage validation.\n"
    )
    if line not in changelog:
        marker = "### Changed\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG Changed anchor drifted")
        changelog = changelog.replace(marker, marker + "\n" + line, 1)
        _write(changelog_path, changelog)


def main() -> None:
    """Apply reversal receipt state, database lineage, tests, and documentation."""
    normalize_reference_reversal_receipts()
    normalize_postgres_reversal_receipts()
    enforce_database_reversal_receipt_lineage()
    add_reversal_receipt_regressions()
    update_documentation()


if __name__ == "__main__":
    main()
