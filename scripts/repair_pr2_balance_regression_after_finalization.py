"""Keep the database-balance regression independent from finalized-journal guards."""

from __future__ import annotations

from pathlib import Path
import re


def main() -> None:
    """Replace the balance regression with an unfinalized direct-SQL journal fixture."""
    path = Path("tests/test_postgres_posting.py")
    text = path.read_text(encoding="utf-8")
    replacement = '''    def test_database_rejects_unbalanced_journal_at_commit(self) -> None:
        """An unfinalized direct-SQL journal cannot commit with unequal debit and credit."""
        journal_reference = f"urn:cwl:accounting:general_journal:unbalanced:{uuid.uuid4()}"
        connection = psycopg.connect(DATABASE_URL)
        try:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            legal_entity_id, book_id, period_id = connection.execute(
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
                """,
                (self.tenant_id,),
            ).fetchone()
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
                    f"database-balance:{journal_reference}",
                    "sha256:" + "d" * 64,
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
                VALUES (
                    %s, %s, %s, %s, %s, 'posted', 'KRW', 'KRW', %s, %s, %s,
                    'ifrs-v1', 'billing-issued-v1'
                )
                RETURNING general_journal_id
                """,
                (
                    self.tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    journal_reference,
                    date(2026, 8, 31),
                    date(2026, 8, 31),
                    proposal_record_id,
                ),
            ).fetchone()[0]
            chart_account_id = connection.execute(
                """
                SELECT chart_account_id
                  FROM accounting_core.chart_account
                 WHERE tenant_account_id = %s
                   AND accounting_book_id = %s
                   AND chart_account_code = '110100'
                   AND valid_to IS NULL
                """,
                (self.tenant_id, book_id),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.journal_entry_line (
                    tenant_account_id, general_journal_id, line_number, chart_account_id,
                    account_role_code, debit_amount, credit_amount, line_description
                )
                VALUES (%s, %s, 1, %s, 'accounts_receivable', 1, 0, 'invalid direct SQL')
                """,
                (self.tenant_id, journal_id, chart_account_id),
            )
            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "journal_unbalanced",
            ):
                connection.commit()
            connection.rollback()
        finally:
            connection.close()

        with psycopg.connect(DATABASE_URL) as verification:
            verification.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            stored_count = verification.execute(
                """
                SELECT count(*)
                  FROM accounting_core.general_journal
                 WHERE tenant_account_id = %s
                   AND journal_reference = %s
                """,
                (self.tenant_id, journal_reference),
            ).fetchone()[0]
        self.assertEqual(stored_count, 0)

'''
    pattern = re.compile(
        r"(?ms)^    def test_database_rejects_unbalanced_journal_at_commit\(self\) -> None:\n"
        r".*?(?=^    def test_soft_close_guc_requires_database_role_membership\()"
    )
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit("database-balance regression replacement count must be exactly one")
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
