"""One-shot repair for remaining exact-input and audit-evidence review contracts."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def restrict_period_close_status() -> None:
    """Accept only the two supported period-close target states."""
    path = "src/accounting_information_platform/accept.py"
    text = _read(path)
    old = '''    period_status_code = payload["period_status_code"]
    if type(period_status_code) is not str or not period_status_code:
        raise AccountingValidationError(
            "period_status_code must be soft_closed or hard_closed. "
            "Omit the field to hard-close, or supply soft_closed or hard_closed, "
            "then retry the close."
        )
    return period_status_code
'''
    new = '''    period_status_code = payload["period_status_code"]
    if type(period_status_code) is not str or period_status_code not in {
        "soft_closed",
        "hard_closed",
    }:
        raise AccountingValidationError(
            "period_status_code must be soft_closed or hard_closed. "
            "Omit the field to hard-close, or supply soft_closed or hard_closed, "
            "then retry the close."
        )
    return period_status_code
'''
    if new in text:
        return
    if old not in text:
        raise SystemExit("period-close status allowlist anchor drifted")
    _write(path, text.replace(old, new, 1))


def require_exact_adjusting_amount_strings() -> None:
    """Reject JSON numbers so adjusting-journal money stays exact at the API boundary."""
    path = "src/accounting_information_platform/accept.py"
    text = _read(path)
    new = '''        raw_amount = raw_line.get("amount")
        if type(raw_amount) is not str:
            raise AccountingValidationError(
                "journal line amount must be an exact decimal string. "
                "Supply amount as a JSON string, then retry the journal post."
            )
        amount = _parse_amount(raw_amount)
'''
    if new in text:
        return
    old = '''        amount = _parse_amount(str(raw_line.get("amount") or ""))
'''
    if old not in text:
        raise SystemExit("adjusting-journal exact-amount anchor drifted")
    _write(path, text.replace(old, new, 1))


def persist_adjusting_journal_description() -> None:
    """Store the required adjusting-journal narrative with the posted journal header."""
    migration_path = "database/migrations/0001_accounting_foundation.sql"
    migration = _read(migration_path)
    desired = '''    accounting_date date NOT NULL,
    journal_description text,
    source_proposal_record_id uuid NOT NULL,
'''
    if desired not in migration:
        anchor = '''    accounting_date date NOT NULL,
    source_proposal_record_id uuid NOT NULL,
'''
        if anchor not in migration:
            raise SystemExit("general_journal description column anchor drifted")
        migration = migration.replace(anchor, desired, 1)
        _write(migration_path, migration)

    accept_path = "src/accounting_information_platform/accept.py"
    accept = _read(accept_path)
    desired_call = '''        transaction_currency=transaction_currency,
        journal_description=journal_description,
        lines=lines,
    )
'''
    if desired_call not in accept:
        call_anchor = '''        transaction_currency=transaction_currency,
        lines=lines,
    )
'''
        start = accept.index("    ledger.post_adjusting_journal(")
        end = accept.index("    return ledger.load_published_receipt_by_key", start)
        block = accept[start:end]
        if call_anchor not in block:
            raise SystemExit("adjusting-journal description call anchor drifted")
        block = block.replace(call_anchor, desired_call, 1)
        accept = accept[:start] + block + accept[end:]
        _write(accept_path, accept)

    persistence_path = "src/accounting_information_platform/persistence.py"
    persistence = _read(persistence_path)
    signature_old = '''        proposal_id: str,
        transaction_currency: str,
        lines: tuple[PostedJournalLine, ...],
    ) -> None:
'''
    signature_new = '''        proposal_id: str,
        transaction_currency: str,
        journal_description: str,
        lines: tuple[PostedJournalLine, ...],
    ) -> None:
'''
    post_start = persistence.index("    def post_adjusting_journal(")
    post_end = persistence.index("    def resolve_accounting_policy(", post_start)
    post_block = persistence[post_start:post_end]
    if signature_new not in post_block:
        if signature_old not in post_block:
            raise SystemExit("post_adjusting_journal signature anchor drifted")
        post_block = post_block.replace(signature_old, signature_new, 1)
    insert_call_old = '''                proposal_record_id=proposal_record_id,
                lines=lines,
            )
'''
    insert_call_new = '''                proposal_record_id=proposal_record_id,
                lines=lines,
                journal_description=journal_description,
            )
'''
    if insert_call_new not in post_block:
        if insert_call_old not in post_block:
            raise SystemExit("post_adjusting_journal insert call anchor drifted")
        post_block = post_block.replace(insert_call_old, insert_call_new, 1)
    persistence = persistence[:post_start] + post_block + persistence[post_end:]

    insert_start = persistence.index("    def _insert_journal(")
    insert_end = persistence.index("    def _insert_receipt(", insert_start)
    insert_block = persistence[insert_start:insert_end]
    signature_old = '''        proposal_record_id: UUID,
        lines: tuple[PostedJournalLine, ...],
    ) -> UUID:
'''
    signature_new = '''        proposal_record_id: UUID,
        lines: tuple[PostedJournalLine, ...],
        journal_description: str = "",
    ) -> UUID:
'''
    if signature_new not in insert_block:
        if signature_old not in insert_block:
            raise SystemExit("_insert_journal description signature anchor drifted")
        insert_block = insert_block.replace(signature_old, signature_new, 1)
    sql_old = '''                functional_currency_code, transaction_date, accounting_date,
                source_proposal_record_id, accounting_policy_version, posting_rule_version
            )
            VALUES (%s, %s, %s, %s, %s, 'posted', %s, %s, %s, %s, %s, %s, %s)
'''
    sql_new = '''                functional_currency_code, transaction_date, accounting_date,
                journal_description, source_proposal_record_id,
                accounting_policy_version, posting_rule_version
            )
            VALUES (%s, %s, %s, %s, %s, 'posted', %s, %s, %s, %s, %s, %s, %s, %s)
'''
    if sql_new not in insert_block:
        if sql_old not in insert_block:
            raise SystemExit("_insert_journal description SQL anchor drifted")
        insert_block = insert_block.replace(sql_old, sql_new, 1)
    tuple_old = '''                proposal.transaction_date,
                proposal.accounting_date,
                proposal_record_id,
                policy.accounting_policy_version,
'''
    tuple_new = '''                proposal.transaction_date,
                proposal.accounting_date,
                journal_description or None,
                proposal_record_id,
                policy.accounting_policy_version,
'''
    if tuple_new not in insert_block:
        if tuple_old not in insert_block:
            raise SystemExit("_insert_journal description values anchor drifted")
        insert_block = insert_block.replace(tuple_old, tuple_new, 1)
    persistence = persistence[:insert_start] + insert_block + persistence[insert_end:]
    _write(persistence_path, persistence)


def add_regressions() -> None:
    """Prove status, exact-money input, and adjusting-description persistence."""
    path = "tests/test_postgres_posting.py"
    tests = _read(path)
    marker = "    def test_closed_period_posts_zero_rows(self) -> None:\n"
    regression = '''    def test_adjusting_journal_persists_description_and_rejects_numeric_amount(self) -> None:
        """Adjusting narratives are durable and monetary JSON numbers fail closed."""
        command_key = f"{self.policy.tenant_reference}:adjusting_journal:audit:v1"
        body = self._adjusting_journal_payload(
            idempotency_key=command_key,
            journal_description="Accrue controller-approved adjustment",
        )
        accepted = accept_adjusting_journal(
            body, DATABASE_URL, self.policy.tenant_reference
        )
        self.assertEqual(accepted["posting_status_code"], "posted")
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.tenant_id),),
            )
            stored = connection.execute(
                """
                SELECT general_journal.journal_description
                FROM accounting_core.general_journal
                JOIN accounting_integration.journal_proposal_record
                  ON journal_proposal_record.tenant_account_id
                     = general_journal.tenant_account_id
                 AND journal_proposal_record.proposal_record_id
                     = general_journal.source_proposal_record_id
                WHERE general_journal.tenant_account_id = %s
                  AND journal_proposal_record.idempotency_key = %s
                """,
                (self.tenant_id, command_key),
            ).fetchone()
        self.assertEqual(stored[0], "Accrue controller-approved adjustment")

        numeric = self._adjusting_journal_payload(
            idempotency_key=f"{command_key}:numeric",
        )
        numeric["journal_lines"][0]["amount"] = 25000
        with self.assertRaisesRegex(
            AccountingValidationError, "exact decimal string"
        ):
            accept_adjusting_journal(
                numeric, DATABASE_URL, self.policy.tenant_reference
            )

    def test_period_close_rejects_unknown_target_status(self) -> None:
        """Only soft_closed and hard_closed are accepted command target states."""
        payload = self._period_close_payload(period_status_code="opened")
        with self.assertRaisesRegex(
            AccountingValidationError, "soft_closed or hard_closed"
        ):
            accept_period_close(payload, DATABASE_URL, self.policy.tenant_reference)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"), 0
        )

'''
    if "test_adjusting_journal_persists_description_and_rejects_numeric_amount" not in tests:
        if marker not in tests:
            raise SystemExit("remaining review regression marker drifted")
        tests = tests.replace(marker, regression + marker, 1)
        _write(path, tests)


def update_documentation() -> None:
    """Align data-model and API behavior docs with the strengthened contracts."""
    data_model_path = "docs/DATA_MODEL.md"
    data_model = _read(data_model_path)
    bullet = "- `general_journal`: authoritative posted or reversed header."
    replacement = (
        "- `general_journal`: authoritative posted or reversed header. AIS-owned "
        "adjusting journals retain their required `journal_description` on this header "
        "so the approved narrative is durable audit evidence."
    )
    if replacement not in data_model:
        if bullet not in data_model:
            raise SystemExit("DATA_MODEL general_journal description anchor drifted")
        data_model = data_model.replace(bullet, replacement, 1)
        _write(data_model_path, data_model)

    test_path = "docs/TEST_STRATEGY.md"
    test_doc = _read(test_path)
    entry = (
        "- Adjusting-journal monetary amounts must arrive as exact decimal strings, "
        "the required journal description must persist on the durable journal header, "
        "and unsupported period-close target states must fail before any write.\n"
    )
    if entry not in test_doc:
        test_doc = test_doc.rstrip() + "\n" + entry
        _write(test_path, test_doc.rstrip() + "\n")


def main() -> None:
    """Apply the remaining exact-input and audit-evidence review repairs."""
    restrict_period_close_status()
    require_exact_adjusting_amount_strings()
    persist_adjusting_journal_description()
    add_regressions()
    update_documentation()


if __name__ == "__main__":
    main()
