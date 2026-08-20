"""One-shot repair for HomeTax and reversal command idempotency in PR 2."""

from __future__ import annotations

from pathlib import Path
import re


def read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def replace_method(path: str, start_name: str, next_name: str, replacement: str) -> None:
    """Replace one class method up to the following class method."""
    text = read(path)
    pattern = re.compile(
        rf"(?ms)^    def {re.escape(start_name)}\(.*?(?=^    def {re.escape(next_name)}\()"
    )
    updated, count = pattern.subn(replacement.rstrip() + "\n\n", text, count=1)
    if count != 1:
        raise SystemExit(f"{path}: method {start_name} replacement count={count}")
    write(path, updated)


def update_home_tax_contract() -> None:
    """Make HomeTax commands exactly replayable and conflict-detecting."""
    migration = """BEGIN;

CREATE TABLE accounting_integration.home_tax_submission (
    home_tax_submission_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    submission_idempotency_key text NOT NULL
        CHECK (btrim(submission_idempotency_key) <> ''),
    submission_status_code text NOT NULL CHECK (submission_status_code IN ('rejected')),
    rejection_reason_code text NOT NULL CHECK (
        rejection_reason_code IN (
            'register_unavailable',
            'hometax_credential_missing',
            'hometax_transport_unavailable'
        )
    ),
    as_of_date date NOT NULL,
    closing_amount numeric(38, 6) NOT NULL,
    register_payload_hash text NOT NULL CHECK (register_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),
    UNIQUE (tenant_account_id, submission_idempotency_key),
    UNIQUE (tenant_account_id, home_tax_submission_id)
);

ALTER TABLE accounting_integration.home_tax_submission ENABLE ROW LEVEL SECURITY;

CREATE POLICY home_tax_submission_isolation ON accounting_integration.home_tax_submission
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

COMMIT;
"""
    write("database/migrations/0003_home_tax_submission.sql", migration)

    accept_path = "src/accounting_information_platform/accept.py"
    accept_text = read(accept_path)
    block = '''            rejection_reason_code="register_unavailable",
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
'''
    key_block = '''            rejection_reason_code="register_unavailable",
        )
    submission_idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not submission_idempotency_key:
        raise AccountingValidationError(
            "idempotency_key is required. "
            "Supply the home-tax-submission command idempotency key, then retry."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
'''
    if block not in accept_text:
        raise SystemExit("HomeTax idempotency insertion anchor drifted")
    accept_text = accept_text.replace(block, key_block, 1)
    incomplete = '''    if not _vat_register_is_loadable(register_document):
        rejection_reason_code = "register_unavailable"
    elif not _home_tax_credential_present():
        rejection_reason_code = "hometax_credential_missing"
    else:
        rejection_reason_code = "hometax_transport_unavailable"
    return ledger.persist_home_tax_submission(
'''
    incomplete_new = '''    if not _vat_register_is_loadable(register_document):
        return _rejected_home_tax_document(
            tenant_reference=tenant_reference,
            legal_entity_reference=legal_entity_reference,
            book_reference=book_reference,
            fiscal_period_reference=fiscal_period_reference,
            vat_period_register=register_document,
            rejection_reason_code="register_unavailable",
        )
    if not _home_tax_credential_present():
        rejection_reason_code = "hometax_credential_missing"
    else:
        rejection_reason_code = "hometax_transport_unavailable"
    return ledger.persist_home_tax_submission(
'''
    if incomplete not in accept_text:
        raise SystemExit("HomeTax incomplete-register branch drifted")
    accept_text = accept_text.replace(incomplete, incomplete_new, 1)
    call_tail = '''        register_document=register_document,
        rejection_reason_code=rejection_reason_code,
    )
'''
    call_tail_new = '''        register_document=register_document,
        rejection_reason_code=rejection_reason_code,
        submission_idempotency_key=submission_idempotency_key,
    )
'''
    if call_tail not in accept_text:
        raise SystemExit("HomeTax persistence call drifted")
    write(accept_path, accept_text.replace(call_tail, call_tail_new, 1))

    persistence_method = '''    def persist_home_tax_submission(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        register_document: dict[str, object],
        rejection_reason_code: str,
        submission_idempotency_key: str,
    ) -> dict[str, object]:
        """Persist or exactly replay one rejected HomeTax command receipt."""
        normalized_key = submission_idempotency_key.strip()
        if not normalized_key:
            raise AccountingValidationError(
                "idempotency_key is required. "
                "Supply the home-tax-submission command idempotency key, then retry."
            )
        register_payload_hash = "sha256:" + hashlib.sha256(
            json.dumps(
                register_document, separators=(",", ":"), sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        raw_as_of_date = str(register_document.get("as_of_date") or "")
        if not raw_as_of_date:
            raise AccountingValidationError(
                "HomeTax submission requires a complete VAT register. "
                "Rebuild the period VAT register, then retry the submission."
            )
        as_of_date = date.fromisoformat(raw_as_of_date)
        closing_amount = Decimal(str(register_document.get("closing_amount") or "0"))
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the home-tax-submission",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the home-tax-submission",
            )[0]
            period_id, _period_status, _period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the home-tax-submission",
            )
            connection.execute(
                """
                INSERT INTO accounting_integration.home_tax_submission (
                    tenant_account_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    submission_idempotency_key,
                    submission_status_code,
                    rejection_reason_code,
                    as_of_date,
                    closing_amount,
                    register_payload_hash
                )
                VALUES (%s, %s, %s, %s, %s, 'rejected', %s, %s, %s, %s)
                ON CONFLICT (tenant_account_id, submission_idempotency_key) DO NOTHING
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    normalized_key,
                    rejection_reason_code,
                    as_of_date,
                    closing_amount,
                    register_payload_hash,
                ),
            )
            row = connection.execute(
                """
                SELECT home_tax_submission_id,
                       legal_entity_id,
                       accounting_book_id,
                       fiscal_period_id,
                       submission_status_code,
                       rejection_reason_code,
                       as_of_date,
                       closing_amount,
                       register_payload_hash
                FROM accounting_integration.home_tax_submission
                WHERE tenant_account_id = %s
                  AND submission_idempotency_key = %s
                """,
                (tenant_id, normalized_key),
            ).fetchone()
            actual_identity = (
                row[1],
                row[2],
                row[3],
                str(row[4]),
                str(row[5]),
                row[6],
                Decimal(row[7]),
                str(row[8]),
            )
            expected_identity = (
                legal_entity_id,
                book_id,
                period_id,
                "rejected",
                rejection_reason_code,
                as_of_date,
                closing_amount,
                register_payload_hash,
            )
            if actual_identity != expected_identity:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different HomeTax command"
                )
        return _home_tax_submission_document(
            home_tax_submission_id=str(row[0]),
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            book_reference=accounting_book_reference,
            period_code=period_code,
            vat_period_register=_home_tax_register_view(register_document),
            rejection_reason_code=rejection_reason_code,
        )
'''
    replace_method(
        "src/accounting_information_platform/persistence.py",
        "persist_home_tax_submission",
        "load_home_tax_submissions",
        persistence_method,
    )

    http_path = "src/accounting_information_platform/http_api.py"
    http_text = read(http_path)
    home_tax_except = '''        try:
            document = accept_home_tax_submission(
                payload, self.server.database_url, tenant_header
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
'''
    home_tax_except_new = '''        try:
            document = accept_home_tax_submission(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(409, f"{error}. Supply a new idempotency key, then retry.")
            return
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
'''
    if home_tax_except not in http_text:
        raise SystemExit("HomeTax HTTP error mapping drifted")
    write(http_path, http_text.replace(home_tax_except, home_tax_except_new, 1))


def update_reversal_contract() -> None:
    """Bind reversals to an exact command key and canonical source hash."""
    core_path = "src/accounting_information_platform/core.py"
    core = read(core_path)
    if "import hashlib\n" not in core:
        core = core.replace("import re\n", "import hashlib\nimport json\nimport re\n", 1)
    core = core.replace(
        '''    reversal_of_journal_reference: str | None = None
    reversal_reason_code: str | None = None
''',
        '''    reversal_of_journal_reference: str | None = None
    reversal_reason_code: str | None = None
    reversal_idempotency_key: str | None = None
''',
        1,
    )
    core = core.replace(
        '''        self._reversal_receipts: dict[tuple[str, str], PostingReceipt] = {}
''',
        '''        self._reversal_receipts: dict[
            tuple[str, str], tuple[str, str, str, PostingReceipt]
        ] = {}
''',
        1,
    )
    write(core_path, core)

    reverse_method = '''    def reverse(
        self,
        journal_reference: str,
        reversal_date: date,
        reversal_reason_code: str,
        policy: AccountingPolicy,
        *,
        reversal_idempotency_key: str | None = None,
    ) -> PostingReceipt:
        """Append or exactly replay the opposite of one original journal."""
        _require_code(reversal_reason_code, "reversal reason code")
        if reversal_idempotency_key is None:
            command_key = f"reversal:{journal_reference}"
        else:
            command_key = reversal_idempotency_key.strip()
            if not command_key:
                raise AccountingValidationError(
                    "reversal_idempotency_key must not be empty"
                )
        command_hash = _reversal_command_hash(
            tenant_reference=policy.tenant_reference,
            reversal_idempotency_key=command_key,
            original_journal_reference=journal_reference,
            reversal_date=reversal_date,
            reversal_reason_code=reversal_reason_code,
        )
        reversal_key = self._tenant_cache_key(policy.tenant_reference, journal_reference)
        prior_receipt = self._cached_reversal_receipt(
            policy.tenant_reference,
            journal_reference,
            command_key,
            command_hash,
        )
        if prior_receipt is not None:
            return prior_receipt
        original = self._journals.get(reversal_key)
        if original is None:
            raise AccountingValidationError("journal does not exist")
        if original.reversal_of_journal_reference is not None:
            raise AccountingValidationError("a reversal journal cannot itself be reversed")
        if not policy.permits(reversal_date):
            raise AccountingValidationError("reversal date belongs to a closed fiscal period")
        if (
            original.legal_entity_reference != policy.legal_entity_reference
            or original.accounting_book_reference != policy.accounting_book_reference
        ):
            raise AccountingValidationError("reversal policy scope does not match original journal")
        reversal_reference = f"{journal_reference}:reversal"
        occupant = self._journals.get(
            self._tenant_cache_key(original.tenant_reference, reversal_reference)
        )
        if occupant is not None:
            if (
                occupant.reversal_of_journal_reference == journal_reference
                and occupant.reversal_idempotency_key == command_key
                and occupant.source_payload_hash == command_hash
            ):
                receipt = self._receipt_for_posted_journal(occupant)
                self._reversal_receipts[reversal_key] = (
                    command_key,
                    journal_reference,
                    command_hash,
                    receipt,
                )
                return receipt
            if occupant.reversal_of_journal_reference == journal_reference:
                raise IdempotencyConflictError(
                    "reversal idempotency key was already used with a different payload"
                )
            raise AccountingValidationError(
                "posted journal is immutable. Reverse the existing journal, then post a replacement."
            )
        reversal_lines = tuple(
            PostedJournalLine(
                line_number=line.line_number,
                chart_account_code=line.chart_account_code,
                account_role_code=line.account_role_code,
                debit_amount=line.credit_amount,
                credit_amount=line.debit_amount,
            )
            for line in original.lines
        )
        reversal = PostedJournal(
            journal_reference=reversal_reference,
            tenant_reference=original.tenant_reference,
            legal_entity_reference=original.legal_entity_reference,
            accounting_book_reference=original.accounting_book_reference,
            accounting_date=reversal_date,
            transaction_currency=original.transaction_currency,
            functional_currency=original.functional_currency,
            source_proposal_id=original.source_proposal_id,
            source_payload_hash=command_hash,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            lines=reversal_lines,
            reversal_of_journal_reference=journal_reference,
            reversal_reason_code=reversal_reason_code,
            reversal_idempotency_key=command_key,
        )
        receipt = PostingReceipt(
            receipt_reference=f"{reversal_reference}:receipt",
            journal_reference=reversal_reference,
            posting_status_code="posted",
            source_proposal_id=original.source_proposal_id,
            source_payload_hash=command_hash,
            tenant_reference=original.tenant_reference,
            legal_entity_reference=original.legal_entity_reference,
            accounting_book_reference=original.accounting_book_reference,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            line_count=len(reversal_lines),
            reversal_of_journal_reference=journal_reference,
        )
        self._journals[self._tenant_cache_key(original.tenant_reference, reversal_reference)] = (
            reversal
        )
        self._reversal_receipts[reversal_key] = (
            command_key,
            journal_reference,
            command_hash,
            receipt,
        )
        return receipt
'''
    replace_method(core_path, "reverse", "trial_balance", reverse_method)

    cached_method = '''    def _cached_reversal_receipt(
        self,
        tenant_reference: str,
        journal_reference: str,
        reversal_idempotency_key: str,
        source_payload_hash: str,
    ) -> PostingReceipt | None:
        """Replay only an exact tenant, key, original, and payload-hash command."""
        prior = self._reversal_receipts.get(
            self._tenant_cache_key(tenant_reference, journal_reference)
        )
        if prior is None:
            return None
        prior_key, prior_original, prior_hash, prior_receipt = prior
        if prior_receipt.tenant_reference != tenant_reference:
            return None
        if (
            prior_key != reversal_idempotency_key
            or prior_original != journal_reference
            or prior_hash != source_payload_hash
        ):
            raise IdempotencyConflictError(
                "reversal idempotency key was already used with a different payload"
            )
        return prior_receipt
'''
    replace_method(
        core_path,
        "_cached_reversal_receipt",
        "_receipt_for_posted_journal",
        cached_method,
    )
    core = read(core_path)
    helper = '''def _reversal_command_hash(
    *,
    tenant_reference: str,
    reversal_idempotency_key: str,
    original_journal_reference: str,
    reversal_date: date,
    reversal_reason_code: str,
) -> str:
    """Return the canonical immutable hash for one reversal command."""
    payload = {
        "tenant_reference": tenant_reference,
        "reversal_idempotency_key": reversal_idempotency_key,
        "original_journal_reference": original_journal_reference,
        "reversal_date": reversal_date.isoformat(),
        "reversal_reason_code": reversal_reason_code,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


'''
    if "def _reversal_command_hash(" not in core:
        marker = "def _parse_amount(value: Decimal | str) -> Decimal:\n"
        if marker not in core:
            raise SystemExit("reversal hash helper anchor drifted")
        core = core.replace(marker, helper + marker, 1)
    write(core_path, core)

    persistence_path = "src/accounting_information_platform/persistence.py"
    persistence = read(persistence_path)
    if "    _reversal_command_hash,\n" not in persistence:
        persistence = persistence.replace(
            "    _require_reference,\n",
            "    _require_reference,\n    _reversal_command_hash,\n",
            1,
        )
    write(persistence_path, persistence)

    postgres_reverse = '''    def reverse(
        self,
        journal_reference: str,
        reversal_date: date,
        reversal_reason_code: str,
        policy: AccountingPolicy,
        *,
        reversal_idempotency_key: str | None = None,
    ) -> PostingReceipt:
        """Append or exactly replay the opposite of one original journal."""
        _require_code(reversal_reason_code, "reversal reason code")
        if reversal_idempotency_key is None:
            command_key = f"reversal:{journal_reference}"
        else:
            command_key = reversal_idempotency_key.strip()
            if not command_key:
                raise AccountingValidationError(
                    "reversal_idempotency_key must not be empty"
                )
        command_hash = _reversal_command_hash(
            tenant_reference=policy.tenant_reference,
            reversal_idempotency_key=command_key,
            original_journal_reference=journal_reference,
            reversal_date=reversal_date,
            reversal_reason_code=reversal_reason_code,
        )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            existing = connection.execute(
                """
                SELECT reversal_journal.journal_reference,
                       reversal_proposal.idempotency_key,
                       reversal_proposal.source_payload_hash,
                       original_journal.journal_reference
                FROM accounting_core.journal_reversal
                JOIN accounting_core.general_journal AS original_journal
                  ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND original_journal.general_journal_id = journal_reversal.original_journal_id
                JOIN accounting_core.general_journal AS reversal_journal
                  ON reversal_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND reversal_journal.general_journal_id = journal_reversal.reversal_journal_id
                JOIN accounting_integration.journal_proposal_record AS reversal_proposal
                  ON reversal_proposal.tenant_account_id = reversal_journal.tenant_account_id
                 AND reversal_proposal.proposal_record_id =
                     reversal_journal.source_proposal_record_id
                WHERE journal_reversal.tenant_account_id = %s
                  AND original_journal.journal_reference = %s
                """,
                (tenant_id, journal_reference),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing[1]) != command_key
                    or str(existing[2]) != command_hash
                    or str(existing[3]) != journal_reference
                ):
                    raise IdempotencyConflictError(
                        "reversal idempotency key was already used with a different payload"
                    )
                return self._receipt_for_journal(connection, tenant_id, existing[0])
            prior_command = connection.execute(
                """
                SELECT source_payload_hash
                FROM accounting_integration.journal_proposal_record
                WHERE tenant_account_id = %s AND idempotency_key = %s
                """,
                (tenant_id, command_key),
            ).fetchone()
            if prior_command is not None:
                raise IdempotencyConflictError(
                    "reversal idempotency key was already used with a different payload"
                )
            original = connection.execute(
                """
                SELECT general_journal_id, legal_entity_id, accounting_book_id,
                       transaction_currency_code, functional_currency_code,
                       source_proposal_record_id, transaction_date
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (tenant_id, journal_reference),
            ).fetchone()
            if original is None:
                raise AccountingValidationError(
                    "journal does not exist. Supply a posted journal reference, then retry reversal."
                )
            already_reversal = connection.execute(
                """
                SELECT 1
                FROM accounting_core.journal_reversal
                WHERE tenant_account_id = %s AND reversal_journal_id = %s
                """,
                (tenant_id, original[0]),
            ).fetchone()
            if already_reversal is not None:
                raise AccountingValidationError(
                    "a reversal journal cannot itself be reversed. Reverse the original journal, or post a replacement."
                )
            if not policy.permits(reversal_date):
                raise AccountingValidationError("reversal date belongs to a closed fiscal period")
            if (
                self._tenant_reference != policy.tenant_reference
                or self._legal_entity_code(connection, tenant_id, original[1])
                != policy.legal_entity_reference
                or self._book_name(connection, tenant_id, original[2])
                != policy.accounting_book_reference
            ):
                raise AccountingValidationError(
                    "reversal policy scope does not match original journal"
                )
            period_id = self._require_adjusting_period(connection, tenant_id, reversal_date)
            original_lines = self._load_lines(connection, tenant_id, original[0])
            reversal_lines = tuple(
                PostedJournalLine(
                    line_number=line.line_number,
                    chart_account_code=line.chart_account_code,
                    account_role_code=line.account_role_code,
                    debit_amount=line.credit_amount,
                    credit_amount=line.debit_amount,
                )
                for line in original_lines
            )
            reversal_reference = f"{journal_reference}:reversal"
            occupant = connection.execute(
                """
                SELECT 1
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (tenant_id, reversal_reference),
            ).fetchone()
            if occupant is not None:
                raise AccountingValidationError(
                    "posted journal is immutable. Reverse the existing journal, "
                    "then post a replacement."
                )
            _source_hash, source_proposal_id = self._proposal_identity(
                connection, tenant_id, original[5]
            )
            receipt = PostingReceipt(
                receipt_reference=f"{reversal_reference}:receipt",
                journal_reference=reversal_reference,
                posting_status_code="posted",
                source_proposal_id=source_proposal_id,
                source_payload_hash=command_hash,
                tenant_reference=policy.tenant_reference,
                legal_entity_reference=policy.legal_entity_reference,
                accounting_book_reference=policy.accounting_book_reference,
                accounting_policy_version=policy.accounting_policy_version,
                posting_rule_version=policy.posting_rule_version,
                line_count=len(reversal_lines),
                reversal_of_journal_reference=journal_reference,
            )
            reversal_proposal_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, uuidv7(), 1, %s, %s, 'posted', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (tenant_id, command_key, command_hash),
            ).fetchone()[0]
            reversal_journal_id = self._insert_journal(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=original[1],
                book_id=original[2],
                period_id=period_id,
                journal_reference=reversal_reference,
                proposal=_ReversalProposal(
                    source_payload_hash=command_hash,
                    transaction_currency=original[3],
                    transaction_date=original[6],
                    accounting_date=reversal_date,
                    source_event_references=(),
                ),
                policy=policy,
                proposal_record_id=reversal_proposal_id,
                lines=reversal_lines,
            )
            connection.execute(
                """
                INSERT INTO accounting_core.journal_reversal (
                    tenant_account_id, original_journal_id, reversal_journal_id,
                    reversal_reason_code
                )
                VALUES (%s, %s, %s, %s)
                """,
                (tenant_id, original[0], reversal_journal_id, reversal_reason_code),
            )
            self._insert_receipt(
                connection, tenant_id, reversal_proposal_id, reversal_journal_id, receipt
            )
            self._insert_outbox(
                connection,
                tenant_id,
                "journal_reversal",
                reversal_reference,
                receipt.receipt_reference,
                receipt,
            )
            return receipt
'''
    replace_method(
        persistence_path,
        "reverse",
        "load_reversal_policy",
        postgres_reverse,
    )

    accept_path = "src/accounting_information_platform/accept.py"
    accept = read(accept_path)
    original = '''    reversal_date = _parse_reversal_date(str(payload.get("reversal_date") or ""))
    ledger = PostgresPostingLedger(database_url, tenant_reference)
'''
    replacement = '''    reversal_date = _parse_reversal_date(str(payload.get("reversal_date") or ""))
    reversal_idempotency_key = str(
        payload.get("reversal_idempotency_key") or f"reversal:{journal_reference}"
    ).strip()
    if not reversal_idempotency_key:
        raise AccountingValidationError(
            "reversal_idempotency_key must not be empty"
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
'''
    if original not in accept:
        raise SystemExit("reversal command anchor drifted")
    accept = accept.replace(original, replacement, 1)
    call = '''    ledger.reverse(journal_reference, reversal_date, reversal_reason_code, policy)
    return ledger.load_published_receipt_by_key(f"reversal:{journal_reference}")
'''
    call_new = '''    ledger.reverse(
        journal_reference,
        reversal_date,
        reversal_reason_code,
        policy,
        reversal_idempotency_key=reversal_idempotency_key,
    )
    return ledger.load_published_receipt_by_key(reversal_idempotency_key)
'''
    if call not in accept:
        raise SystemExit("reversal persistence call drifted")
    write(accept_path, accept.replace(call, call_new, 1))

    http_path = "src/accounting_information_platform/http_api.py"
    http = read(http_path)
    reversal_except = '''        try:
            document = accept_journal_reversal(
                payload, self.server.database_url, tenant_header
            )
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
'''
    reversal_except_new = '''        try:
            document = accept_journal_reversal(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(409, f"{error}. Supply a new reversal_idempotency_key, then retry.")
            return
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
'''
    if reversal_except not in http:
        raise SystemExit("reversal HTTP error mapping drifted")
    write(http_path, http.replace(reversal_except, reversal_except_new, 1))


def update_tests() -> None:
    """Add exact replay, conflict, and no-date-min regression evidence."""
    core_path = "tests/test_accounting_core.py"
    core_tests = read(core_path)
    foreign_pattern = re.compile(
        r'''(?ms)        self\.ledger\._reversal_receipts\[reversal_key\] = PostingReceipt\(
.*?^        \)
'''
    )
    match = foreign_pattern.search(core_tests)
    if match is None:
        raise SystemExit("foreign reversal cache fixture drifted")
    original_assignment = match.group(0)
    receipt_expression = original_assignment.split("= ", 1)[1].rstrip()
    tuple_assignment = (
        "        self.ledger._reversal_receipts[reversal_key] = (\n"
        "            f\"reversal:{first.journal_reference}\",\n"
        "            first.journal_reference,\n"
        "            reversal.source_payload_hash,\n"
        + "\n".join("            " + line for line in receipt_expression.splitlines())
        + ",\n"
        "        )\n"
    )
    core_tests = (
        core_tests[: match.start()] + tuple_assignment + core_tests[match.end() :]
    )
    marker = "    def test_same_proposal_id_posts_independently_per_tenant(self) -> None:\n"
    new_test = '''    def test_reversal_command_replay_requires_key_original_and_hash(self) -> None:
        """Changed reversal commands fail closed for cache and occupant replay."""
        first = self.ledger.post(self._invoice_proposal(), self.policy)
        command_key = "reversal:invoice-1:correction:v1"
        reversal = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key=command_key,
        )
        replay = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key=command_key,
        )
        self.assertEqual(replay, reversal)
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "duplicate_charge",
                self.policy,
                reversal_idempotency_key=command_key,
            )
        del self.ledger._reversal_receipts[
            self.ledger._tenant_cache_key(
                self.policy.tenant_reference, first.journal_reference
            )
        ]
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "duplicate_charge",
                self.policy,
                reversal_idempotency_key=command_key,
            )
        with self.assertRaisesRegex(
            AccountingValidationError, "reversal_idempotency_key"
        ):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
                reversal_idempotency_key="",
            )

'''
    if "test_reversal_command_replay_requires_key_original_and_hash" not in core_tests:
        if marker not in core_tests:
            raise SystemExit("core reversal test insertion marker drifted")
        core_tests = core_tests.replace(marker, new_test + marker, 1)
    write(core_path, core_tests)

    postgres_path = "tests/test_postgres_posting.py"
    tests = read(postgres_path)
    reverse_marker = "    def test_closed_period_posts_zero_rows(self) -> None:\n"
    postgres_reversal_test = '''    def test_reversal_command_key_and_hash_are_exact(self) -> None:
        """PostgreSQL replays only one exact reversal command."""
        first = self.ledger.post(self._two_line_proposal(), self.policy)
        command_key = f"{self.policy.tenant_reference}:reversal:invoice:v1"
        reversal = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key=command_key,
        )
        replay = self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key=command_key,
        )
        self.assertEqual(replay, reversal)
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "duplicate_charge",
                self.policy,
                reversal_idempotency_key=command_key,
            )

        second = self.ledger.post(
            self._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key=f"{self.policy.tenant_reference}:second_invoice:v1",
                source_payload_hash="sha256:" + "9" * 64,
            ),
            self.policy,
        )
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                second.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
                reversal_idempotency_key=command_key,
            )

'''
    if "test_reversal_command_key_and_hash_are_exact" not in tests:
        if reverse_marker not in tests:
            raise SystemExit("PostgreSQL reversal test insertion marker drifted")
        tests = tests.replace(reverse_marker, postgres_reversal_test + reverse_marker, 1)

    old_helper_signature = '''    def _http_home_tax_submission(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
'''
    new_helper_signature = '''    def _http_home_tax_submission(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        idempotency_key: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
'''
    if old_helper_signature in tests:
        tests = tests.replace(old_helper_signature, new_helper_signature, 1)
    elif new_helper_signature not in tests:
        raise SystemExit("HomeTax HTTP helper signature drifted")
    payload_tail = '''            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
'''
    payload_tail_new = '''            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
            "idempotency_key": (
                idempotency_key
                if idempotency_key is not None
                else f"{self.policy.tenant_reference}:home_tax_submission:{uuid.uuid4()}"
            ),
        }
'''
    helper_start = tests.index(new_helper_signature)
    helper_end = tests.index("    def _http_home_tax_submissions(", helper_start)
    helper_text = tests[helper_start:helper_end]
    if payload_tail not in helper_text and '            "idempotency_key":' not in helper_text:
        raise SystemExit("HomeTax HTTP helper payload drifted")
    helper_text = helper_text.replace(payload_tail, payload_tail_new, 1)
    tests = tests[:helper_start] + helper_text + tests[helper_end:]

    incomplete_old = '''        self.assertEqual(listed_status, 200)
        self.assertEqual(len(listed["home_tax_submissions"]), 1)
        self.assertEqual(
            listed["home_tax_submissions"][0]["rejection_reason_code"],
            "register_unavailable",
        )
'''
    incomplete_new = '''        self.assertEqual(listed_status, 200)
        self.assertEqual(listed["home_tax_submissions"], [])
'''
    if incomplete_old not in tests and incomplete_new not in tests:
        raise SystemExit("incomplete HomeTax persistence assertion drifted")
    tests = tests.replace(incomplete_old, incomplete_new, 1)

    insertion_marker = (
        "    def test_http_rejects_home_tax_submission_when_transport_unavailable(self) -> None:\n"
    )
    home_tax_replay_test = '''    def test_home_tax_submission_idempotency_replays_and_conflicts(self) -> None:
        """The same HomeTax command replays; changed register evidence conflicts."""
        server = self._start_http_server()
        command_key = f"{self.policy.tenant_reference}:home_tax_submission:august:v1"
        first_status, first = self._http_home_tax_submission(
            idempotency_key=command_key
        )
        replay_status, replay = self._http_home_tax_submission(
            idempotency_key=command_key
        )
        self.assertEqual(first_status, 422)
        self.assertEqual(replay_status, 422)
        self.assertEqual(replay, first)
        self.assertEqual(
            self._count_table("accounting_integration.home_tax_submission"), 1
        )

        taxed_status, _taxed = self._http_json(
            "POST", "/journal-proposals", self._billing_taxed_payload()
        )
        conflict_status, _conflict = self._http_home_tax_submission(
            idempotency_key=command_key
        )
        self.assertEqual(taxed_status, 200)
        self.assertEqual(conflict_status, 409)
        self.assertEqual(
            self._count_table("accounting_integration.home_tax_submission"), 1
        )

        missing_key_status, _missing_key = self._http_json(
            "POST",
            "/home-tax-submissions",
            {
                "tenant_reference": self.policy.tenant_reference,
                "legal_entity_reference": self.policy.legal_entity_reference,
                "book_reference": self.policy.accounting_book_reference,
                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            },
        )
        self.assertEqual(missing_key_status, 404)
        server.shutdown()

'''
    if "test_home_tax_submission_idempotency_replays_and_conflicts" not in tests:
        if insertion_marker not in tests:
            raise SystemExit("HomeTax replay test insertion marker drifted")
        tests = tests.replace(
            insertion_marker, home_tax_replay_test + insertion_marker, 1
        )
    write(postgres_path, tests)


def update_docs() -> None:
    """Record exact command replay boundaries in ADR and release notes."""
    adr_path = "docs/adr/0003-append-only-journals.md"
    adr = read(adr_path)
    sentence = (
        "If the occupant is the existing reversing journal for that original, "
        "the same reversal request still replays."
    )
    replacement = (
        "If the occupant is the existing reversing journal for that original, "
        "the request replays only when tenant_reference, reversal idempotency key, "
        "original journal_reference, and immutable canonical source-payload hash all "
        "match; any mismatch fails closed."
    )
    if sentence not in adr:
        raise SystemExit("ADR reversal replay sentence drifted")
    write(adr_path, adr.replace(sentence, replacement, 1))

    changelog_path = "CHANGELOG.md"
    changelog = read(changelog_path)
    entry = (
        "- Made HomeTax rejection commands and journal reversals exactly idempotent: "
        "tenant-scoped command keys replay only identical scope and canonical payload "
        "hashes, while changed evidence or reversal intent fails closed.\n"
    )
    if entry not in changelog:
        marker = "### Changed\n"
        if marker in changelog:
            changelog = changelog.replace(marker, marker + "\n" + entry, 1)
        else:
            changelog = changelog.rstrip() + "\n\n" + entry
    write(changelog_path, changelog.rstrip() + "\n")


def main() -> None:
    """Apply command idempotency and exact-replay repairs."""
    update_home_tax_contract()
    update_reversal_contract()
    update_tests()
    update_docs()


if __name__ == "__main__":
    main()
