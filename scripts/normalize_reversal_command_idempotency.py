"""One-shot exact-head normalization for durable reversal-command idempotency."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def _replace_method(path: str, name: str, next_name: str, replacement: str) -> None:
    text = _read(path)
    pattern = re.compile(
        rf"(?ms)^    def {re.escape(name)}\(.*?(?=^    def {re.escape(next_name)}\()"
    )
    updated, count = pattern.subn(replacement.rstrip() + "\n\n", text, count=1)
    if count != 1:
        raise SystemExit(f"{path}: expected one {name} method, found {count}")
    _write(path, updated)


def update_persistence() -> None:
    """Make durable reversal replay depend on command key plus immutable command hash."""
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)
    if "    _reversal_command_hash,\n" not in text:
        anchor = "    _require_reference,\n"
        if anchor not in text:
            raise SystemExit("persistence core import anchor drifted")
        text = text.replace(anchor, anchor + "    _reversal_command_hash,\n", 1)
        _write(path, text)

    method = '''    def reverse(
        self,
        journal_reference: str,
        reversal_date: date,
        reversal_reason_code: str,
        policy: AccountingPolicy,
        *,
        reversal_idempotency_key: str,
    ) -> PostingReceipt:
        """Append or exactly replay one immutable reversal command."""
        _require_code(reversal_reason_code, "reversal reason code")
        command_key = reversal_idempotency_key.strip()
        if not command_key:
            raise AccountingValidationError(
                "reversal_idempotency_key is required. "
                "Supply the reversal command idempotency key, then retry reversal."
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
                       original_journal.journal_reference,
                       journal_reversal.reversal_reason_code,
                       reversal_journal.accounting_date
                FROM accounting_core.journal_reversal
                JOIN accounting_core.general_journal AS original_journal
                  ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND original_journal.general_journal_id = journal_reversal.original_journal_id
                JOIN accounting_core.general_journal AS reversal_journal
                  ON reversal_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND reversal_journal.general_journal_id = journal_reversal.reversal_journal_id
                JOIN accounting_integration.journal_proposal_record AS reversal_proposal
                  ON reversal_proposal.tenant_account_id = reversal_journal.tenant_account_id
                 AND reversal_proposal.proposal_record_id = reversal_journal.source_proposal_record_id
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
                    or str(existing[4]) != reversal_reason_code
                    or existing[5] != reversal_date
                ):
                    raise IdempotencyConflictError(
                        "reversal idempotency key or command evidence conflicts with the retained reversal"
                    )
                return self._receipt_for_journal(connection, tenant_id, str(existing[0]))

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
                    "reversal idempotency key was already used by another accounting command"
                )

            original = connection.execute(
                """
                SELECT general_journal_id, legal_entity_id, accounting_book_id,
                       transaction_currency_code, functional_currency_code,
                       source_proposal_record_id, transaction_date, accounting_date
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
            if reversal_date < original[7]:
                raise AccountingValidationError(
                    "reversal date must not precede the original journal accounting date"
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
                    "posted journal is immutable. Reverse the existing journal, then post a replacement."
                )

            _original_source_hash, source_proposal_id = self._proposal_identity(
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
    _replace_method(path, "reverse", "load_reversal_policy", method)


def update_accept_boundary() -> None:
    """Require a reversal-command key distinct from the optional original-journal locator."""
    path = "src/accounting_information_platform/accept.py"
    method = '''def accept_journal_reversal(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Reverse one posted journal under an explicit immutable command identity."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "journal reversal payload must be a JSON object. "
            "Supply a journal-reversal command, then retry the reverse."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "reversal tenant_reference does not match the bound tenant. "
            "Call accept_journal_reversal with that tenant_reference, then retry."
        )
    journal_reference = str(payload.get("journal_reference") or "")
    idempotency_key = str(payload.get("idempotency_key") or "")
    reversal_idempotency_key = str(payload.get("reversal_idempotency_key") or "").strip()
    reversal_reason_code = str(payload.get("reversal_reason_code") or "")
    if not journal_reference and not idempotency_key:
        raise AccountingValidationError(
            "journal_reference or idempotency_key is required. "
            "Supply the posted journal or the Billing idempotency key, then retry the reverse."
        )
    if not reversal_idempotency_key:
        raise AccountingValidationError(
            "reversal_idempotency_key is required. "
            "Supply the reversal command idempotency key, then retry the reverse."
        )
    if not reversal_reason_code:
        raise AccountingValidationError(
            "reversal_reason_code is required. "
            "Supply a reversal reason code, then retry the reverse."
        )
    reversal_date = _parse_reversal_date(str(payload.get("reversal_date") or ""))
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    if idempotency_key:
        original = ledger.load_published_receipt_by_key(idempotency_key)
        resolved_reference = str(original["journal_reference"])
        if journal_reference and journal_reference != resolved_reference:
            raise AccountingValidationError(
                "journal_reference and idempotency_key do not match the same posted journal. "
                "Supply one identity, then retry the reverse."
            )
        journal_reference = resolved_reference
    policy = ledger.load_reversal_policy(journal_reference, reversal_date)
    ledger.reverse(
        journal_reference,
        reversal_date,
        reversal_reason_code,
        policy,
        reversal_idempotency_key=reversal_idempotency_key,
    )
    return ledger.load_published_receipt_by_key(reversal_idempotency_key)
'''
    text = _read(path)
    pattern = re.compile(
        r"(?ms)^def accept_journal_reversal\(.*?(?=^def accept_period_close\()"
    )
    updated, count = pattern.subn(method.rstrip() + "\n\n", text, count=1)
    if count != 1:
        raise SystemExit(f"{path}: reversal accept method drifted")
    _write(path, updated)


def update_http_boundary() -> None:
    """Map a durable reversal replay conflict to HTTP 409 before generic validation."""
    path = "src/accounting_information_platform/http_api.py"
    text = _read(path)
    old = '''        try:
            document = accept_journal_reversal(
                payload, self.server.database_url, tenant_header
            )
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
'''
    new = '''        try:
            document = accept_journal_reversal(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(
                409,
                f"{error}. Supply a new reversal_idempotency_key, then retry.",
            )
            return
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
'''
    if new not in text:
        if old not in text:
            raise SystemExit("HTTP reversal error mapping anchor drifted")
        text = text.replace(old, new, 1)
    _write(path, text)


def update_adr() -> None:
    """Align the public HTTP contract with the explicit reversal-command key."""
    path = "docs/adr/0012-http-append-only-reversal.md"
    text = _read(path)
    old = "The reversal command has a tenant-scoped deterministic retry identity `reversal:{journal_reference}` unless an internal caller supplies an explicit reversal command idempotency key. Its immutable command hash binds all of the following together:"
    new = "Every public reversal command requires a distinct tenant-scoped `reversal_idempotency_key`; the optional Billing `idempotency_key` remains only an original-journal locator and is never reused as the reversal command identity. Internal persistence paths require the same explicit reversal command key. Its immutable command hash binds all of the following together:"
    if old not in text:
        if new not in text:
            raise SystemExit("ADR 0012 command-key paragraph drifted")
    else:
        text = text.replace(old, new, 1)
    _write(path, text)


def main() -> None:
    update_persistence()
    update_accept_boundary()
    update_http_boundary()
    update_adr()


if __name__ == "__main__":
    main()
