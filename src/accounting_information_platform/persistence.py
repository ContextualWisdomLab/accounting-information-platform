"""PostgreSQL adapter that preserves PostingLedger invariants on durable rows."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Mapping
from uuid import UUID

from .core import (
    AccountBalance,
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalProposal,
    PeriodCloseReceipt,
    PostedJournalLine,
    PostingLedger,
    PostingReceipt,
    _reversal_command_hash,
    _require_code,
    _require_currency,
    _require_proposal_id,
    _require_reference,
)

_SQL_SKIP_DATE = date.min
_SQL_SKIP_DATETIME = datetime(1, 1, 1, tzinfo=timezone.utc)
_SQL_SKIP_UUID = UUID(int=0)
_CLOSING_JOURNAL_PATTERN = "urn:cwl:accounting:general_journal:period_closing:%"


class PostgresPostingLedger:
    """Authoritative posting, catalog policy resolution, close, trial balance, and statements on PostgreSQL 18."""

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        """Bind one tenant to a PostgreSQL 18 database URL."""
        if not database_url:
            raise AccountingValidationError(
                "ACCOUNTING_DATABASE_URL is empty. Set a PostgreSQL 18 URL and retry posting."
            )
        _require_reference(tenant_reference, "tenant reference")
        self._database_url = database_url
        self._tenant_reference = tenant_reference
        self._active_connection: object | None = None

    @property
    def journal_count(self) -> int:
        """Return the number of original and reversal journals retained for the tenant."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s
                """,
                (tenant_id,),
            ).fetchone()
        return int(row[0])

    def post(self, proposal: JournalProposal, policy: AccountingPolicy) -> PostingReceipt:
        """Persist *proposal* using a caller-supplied policy, or return its prior receipt."""
        return self._persist_proposal(proposal, policy)

    def post_proposal(self, proposal: JournalProposal) -> PostingReceipt:
        """Resolve AIS catalog policy and persist *proposal*, or return its prior receipt."""
        return self._persist_proposal(proposal, None)

    def post_adjusting_journal(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        journal_date: date,
        idempotency_key: str,
        source_payload_hash: str,
        proposal_id: str,
        transaction_currency: str,
        lines: tuple[PostedJournalLine, ...],
    ) -> None:
        """Persist one AIS-owned adjusting journal through the ordinary post tables."""
        proposal_uuid = _require_proposal_uuid(proposal_id)
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(connection, f"adjusting:{idempotency_key}")
            prior = connection.execute(
                """
                SELECT source_payload_hash
                FROM accounting_integration.journal_proposal_record
                WHERE tenant_account_id = %s AND idempotency_key = %s
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[0] != source_payload_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different payload"
                    )
                return
            legal_entity_id, functional_currency = self._load_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the journal post",
            )
            book_row = connection.execute(
                """
                SELECT accounting_book_id, reporting_currency_code, book_role_code
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s
                  AND legal_entity_id = %s
                  AND book_name = %s
                  AND valid_to IS NULL
                """,
                (tenant_id, legal_entity_id, accounting_book_reference),
            ).fetchone()
            if book_row is None:
                raise AccountingValidationError(
                    f"Accounting book {accounting_book_reference} is not recorded for this legal entity. "
                    "Create the accounting_book row, then retry the journal post."
                )
            book_id, reporting_currency_code, book_role_code = book_row
            if transaction_currency != reporting_currency_code:
                raise AccountingValidationError(
                    f"currency {transaction_currency} does not match book reporting "
                    f"currency {reporting_currency_code}. Supply the book reporting currency, "
                    "then retry the journal post."
                )
            period_state = self._load_book_period_state(
                connection, tenant_id, book_id, period_code
            )
            if period_state is None:
                raise AccountingValidationError(
                    f"Fiscal period {period_code} is not recorded for this tenant. "
                    "Create the fiscal_period row, then retry the journal post."
                )
            period_id, period_status_code, period_start, period_end = period_state
            if journal_date < period_start or journal_date > period_end:
                raise AccountingValidationError(
                    "journal_date must fall inside the supplied fiscal period. "
                    "Supply a journal_date in that period, then retry the journal post."
                )
            if period_status_code == "hard_closed":
                raise AccountingValidationError(
                    f"Fiscal period {period_code} is hard_closed. "
                    "Post the adjusting journal into an open or soft-closed period, "
                    "then retry; no journal was written."
                )
            policy_row = connection.execute(
                """
                SELECT accounting_policy_version, posting_rule_version
                FROM accounting_core.account_role_mapping
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND valid_to IS NULL
                ORDER BY account_role_code
                LIMIT 1
                """,
                (tenant_id, book_id),
            ).fetchone()
            if policy_row is None:
                raise AccountingValidationError(
                    "No account_role_mapping is effective for this book. "
                    "Create the account_role_mapping rows, then retry the journal post."
                )
            policy = AccountingPolicy(
                tenant_reference=self._tenant_reference,
                legal_entity_reference=legal_entity_reference,
                accounting_book_reference=accounting_book_reference,
                intended_book_role_code=book_role_code,
                transaction_currency=transaction_currency,
                functional_currency=functional_currency,
                open_period_start=period_start,
                open_period_end=period_end,
                chart_account_mapping={},
                accounting_policy_version=policy_row[0],
                posting_rule_version=policy_row[1],
            )
            proposal = _AdjustingProposal(
                source_payload_hash=source_payload_hash,
                transaction_currency=transaction_currency,
                transaction_date=journal_date,
                accounting_date=journal_date,
                source_event_references=(
                    f"urn:cwl:accounting:adjusting_journal:{proposal_id}",
                ),
            )
            journal_reference = f"urn:cwl:accounting:general_journal:{proposal_id}"
            receipt = PostingReceipt(
                receipt_reference=f"urn:cwl:accounting:posting_receipt:{proposal_id}",
                journal_reference=journal_reference,
                posting_status_code="posted",
                source_proposal_id=proposal_id,
                source_payload_hash=source_payload_hash,
                tenant_reference=self._tenant_reference,
                legal_entity_reference=legal_entity_reference,
                accounting_book_reference=accounting_book_reference,
                accounting_policy_version=policy.accounting_policy_version,
                posting_rule_version=policy.posting_rule_version,
                line_count=len(lines),
            )
            proposal_record_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, %s, %s, %s, %s, 'posted', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (
                    tenant_id,
                    proposal_uuid,
                    1,
                    idempotency_key,
                    source_payload_hash,
                ),
            ).fetchone()[0]
            journal_id = self._insert_journal(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
                journal_reference=journal_reference,
                proposal=proposal,
                policy=policy,
                proposal_record_id=proposal_record_id,
                lines=lines,
            )
            self._insert_receipt(
                connection, tenant_id, proposal_record_id, journal_id, receipt
            )
            self._insert_outbox(
                connection,
                tenant_id,
                "posting_receipt",
                journal_reference,
                receipt.receipt_reference,
                receipt,
            )

    def resolve_accounting_policy(self, proposal: JournalProposal) -> AccountingPolicy:
        """Load the effective catalog policy for *proposal* without posting."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            return self._resolve_accounting_policy(connection, tenant_id, proposal)

    def load_published_receipt(self, proposal: JournalProposal) -> dict[str, object]:
        """Return the schema-shaped posting receipt for a persisted *proposal*."""
        return self.load_published_receipt_by_key(proposal.idempotency_key)

    def load_published_receipt_by_key(self, idempotency_key: str) -> dict[str, object]:
        """Return the schema-shaped posting receipt for one Billing idempotency key."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            return self._load_published_receipt(connection, tenant_id, idempotency_key)

    def load_posted_journal(
        self, idempotency_key: str = "", journal_reference: str = ""
    ) -> dict[str, object]:
        """Return one persisted journal and its lines for a tenant key or reference."""
        if not idempotency_key and not journal_reference:
            raise AccountingValidationError(
                "idempotency_key or journal_reference is required. "
                "Supply the Billing key or the posted journal reference, then retry the journal read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            by_key = None
            by_reference = None
            if idempotency_key:
                by_key = self._load_journal_row(
                    connection, tenant_id, idempotency_key=idempotency_key
                )
                if by_key is None:
                    raise AccountingValidationError(
                        "posted journal is missing for this idempotency key. "
                        "Accept the proposal, then retry the journal read."
                    )
            if journal_reference:
                by_reference = self._load_journal_row(
                    connection, tenant_id, journal_reference=journal_reference
                )
                if by_reference is None:
                    raise AccountingValidationError(
                        "posted journal is missing for this journal_reference. "
                        "Accept the proposal, then retry the journal read."
                    )
            if (
                by_key is not None
                and by_reference is not None
                and by_key[0] != by_reference[0]
            ):
                raise AccountingValidationError(
                    "journal_reference and idempotency_key do not match the same posted journal. "
                    "Supply one identity, then retry the journal read."
                )
            row = by_key if by_key is not None else by_reference
            lines = self._load_lines(connection, tenant_id, row[0])
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": row[8],
                "accounting_book_reference": row[9],
                "journal_reference": row[1],
                "idempotency_key": row[10],
                "journal_status_code": row[2],
                "accounting_date": row[3].isoformat(),
                "transaction_currency": row[4],
                "functional_currency": row[5],
                "accounting_policy_version": row[6],
                "posting_rule_version": row[7],
                "source_payload_hash": row[11],
                "source_proposal_id": str(row[12]),
                "reversal_of_journal_reference": row[13],
                "reversal_reason_code": row[14],
                "lines": [
                    {
                        "line_number": line.line_number,
                        "chart_account_code": line.chart_account_code,
                        "account_role_code": line.account_role_code,
                        "debit_amount": _exact_amount_text(line.debit_amount),
                        "credit_amount": _exact_amount_text(line.credit_amount),
                    }
                    for line in lines
                ],
            }

    def load_period_journals(
        self,
        legal_entity_reference: str,
        book_reference: str,
        period_code: str,
        *,
        page_limit: int = 50,
        cursor_after: tuple[date, str] | None = None,
        journal_source_code: str = "",
    ) -> dict[str, object]:
        """Return one page of existing journals for a tenant entity, book, and period, optionally by source."""
        if not legal_entity_reference or not book_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those journal-list fields, then retry the journal list."
            )
        if journal_source_code and journal_source_code not in {
            "billing",
            "adjusting",
            "period_closing",
            "reversal",
        }:
            raise AccountingValidationError(
                "journal_source_code must be billing, adjusting, period_closing, or reversal. "
                "Supply a known journal source, then retry the journal list."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the journal list"
            )
            book_id, _currency = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                book_reference,
                "the journal list",
            )
            period_id, _status, _period_end = self._require_fiscal_period(
                connection, tenant_id, period_code, "the journal list"
            )
            if cursor_after is None:
                skip_cursor, cursor_date, cursor_reference = True, _SQL_SKIP_DATE, ""
            else:
                skip_cursor, cursor_date, cursor_reference = False, cursor_after[0], cursor_after[1]
            rows = connection.execute(
                """
                SELECT general_journal.journal_reference,
                       journal_proposal_record.idempotency_key,
                       general_journal.journal_status_code,
                       general_journal.accounting_date,
                       (
                           SELECT COUNT(*)
                           FROM accounting_core.journal_entry_line
                           WHERE tenant_account_id = general_journal.tenant_account_id
                             AND general_journal_id = general_journal.general_journal_id
                       ),
                       original_journal.journal_reference
                FROM accounting_core.general_journal
                JOIN accounting_integration.journal_proposal_record
                  ON journal_proposal_record.tenant_account_id = general_journal.tenant_account_id
                 AND journal_proposal_record.proposal_record_id
                   = general_journal.source_proposal_record_id
                LEFT JOIN accounting_core.journal_reversal
                  ON journal_reversal.tenant_account_id = general_journal.tenant_account_id
                 AND journal_reversal.reversal_journal_id = general_journal.general_journal_id
                LEFT JOIN accounting_core.general_journal AS original_journal
                  ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND original_journal.general_journal_id = journal_reversal.original_journal_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.legal_entity_id = %s
                  AND general_journal.accounting_book_id = %s
                  AND general_journal.fiscal_period_id = %s
                  AND (
                        %s
                        OR (general_journal.accounting_date, general_journal.journal_reference)
                           > (%s, %s)
                      )
                  AND (
                        %s
                        OR (%s AND journal_reversal.reversal_journal_id IS NOT NULL)
                        OR (%s AND general_journal.journal_reference LIKE %s)
                        OR (
                              %s
                              AND journal_reversal.reversal_journal_id IS NULL
                              AND EXISTS (
                                    SELECT 1
                                    FROM accounting_core.journal_entry_line
                                    WHERE journal_entry_line.tenant_account_id
                                          = general_journal.tenant_account_id
                                      AND journal_entry_line.general_journal_id
                                          = general_journal.general_journal_id
                                      AND journal_entry_line.account_role_code = 'adjusting'
                              )
                        )
                        OR (
                              %s
                              AND journal_reversal.reversal_journal_id IS NULL
                              AND general_journal.journal_reference NOT LIKE %s
                              AND NOT EXISTS (
                                    SELECT 1
                                    FROM accounting_core.journal_entry_line
                                    WHERE journal_entry_line.tenant_account_id
                                          = general_journal.tenant_account_id
                                      AND journal_entry_line.general_journal_id
                                          = general_journal.general_journal_id
                                      AND journal_entry_line.account_role_code = 'adjusting'
                              )
                        )
                      )
                ORDER BY general_journal.accounting_date, general_journal.journal_reference
                LIMIT %s
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    skip_cursor,
                    cursor_date,
                    cursor_reference,
                    journal_source_code == "",
                    journal_source_code == "reversal",
                    journal_source_code == "period_closing",
                    _CLOSING_JOURNAL_PATTERN,
                    journal_source_code == "adjusting",
                    journal_source_code == "billing",
                    _CLOSING_JOURNAL_PATTERN,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            journals = [
                {
                    "journal_reference": row[0],
                    "idempotency_key": row[1],
                    "journal_status_code": row[2],
                    "accounting_date": row[3].isoformat(),
                    "line_count": int(row[4]),
                    "reversal_of_journal_reference": row[5],
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{last[3].isoformat()}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "accounting_book_reference": book_reference,
                "book_reference": book_reference,
                "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
                "period_code": period_code,
                "journals": journals,
                "next_cursor": next_cursor,
            }
            if journal_source_code:
                document["journal_source_code"] = journal_source_code
            return document

    def load_journal_reversals(
        self,
        legal_entity_reference: str,
        original_journal_reference: str = "",
        period_code: str = "",
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, str] | None = None,
    ) -> dict[str, object]:
        """Return one page of existing journal reversals for a tenant legal entity."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the journal-reversal list"
            )
            period_id_value: object = _SQL_SKIP_UUID
            skip_period = True
            if period_code:
                period_id_value = self._require_fiscal_period(
                    connection, tenant_id, period_code, "the journal-reversal list"
                )[0]
                skip_period = False
            if cursor_after is None:
                skip_cursor, cursor_posted_at, cursor_reference = (
                    True,
                    _SQL_SKIP_DATETIME,
                    "",
                )
            else:
                skip_cursor, cursor_posted_at, cursor_reference = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT reversal_journal.journal_reference,
                       original_journal.journal_reference,
                       reversal_journal.accounting_date,
                       reversal_journal.posted_at,
                       journal_reversal.reversal_reason_code
                FROM accounting_core.journal_reversal
                JOIN accounting_core.general_journal AS reversal_journal
                  ON reversal_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND reversal_journal.general_journal_id = journal_reversal.reversal_journal_id
                JOIN accounting_core.general_journal AS original_journal
                  ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND original_journal.general_journal_id = journal_reversal.original_journal_id
                WHERE journal_reversal.tenant_account_id = %s
                  AND reversal_journal.legal_entity_id = %s
                  AND (%s OR original_journal.journal_reference = %s)
                  AND (%s OR reversal_journal.fiscal_period_id = %s)
                  AND (
                        %s
                        OR (reversal_journal.posted_at, reversal_journal.journal_reference)
                           > (%s, %s)
                      )
                ORDER BY reversal_journal.posted_at, reversal_journal.journal_reference
                LIMIT %s
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    not original_journal_reference,
                    original_journal_reference,
                    skip_period,
                    period_id_value,
                    skip_cursor,
                    cursor_posted_at,
                    cursor_reference,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            journal_reversals = [
                {
                    "reversal_journal_reference": row[0],
                    "original_journal_reference": row[1],
                    "reversal_date": row[2].isoformat(),
                    "posted_at": _format_timestamp(row[3]),
                    "reversal_reason_code": row[4],
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[3])}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "journal_reversals": journal_reversals,
                "next_cursor": next_cursor,
            }
            if original_journal_reference:
                document["original_journal_reference"] = original_journal_reference
            if period_code:
                document["fiscal_period_reference"] = (
                    f"urn:cwl:accounting:fiscal_period:{period_code}"
                )
            return document

    def load_period_closes(
        self,
        legal_entity_reference: str,
        period_code: str = "",
        period_status_code: str = "",
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, UUID] | None = None,
    ) -> dict[str, object]:
        """Return one page of durable hard-close receipts for a tenant legal entity."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the period-close list"
            )
            period_id_value: object = _SQL_SKIP_UUID
            skip_period = True
            if period_code:
                period_id_value = self._require_fiscal_period(
                    connection, tenant_id, period_code, "the period-close list"
                )[0]
                skip_period = False
            if cursor_after is None:
                skip_cursor, cursor_generated_at, cursor_snapshot_id = (
                    True,
                    _SQL_SKIP_DATETIME,
                    _SQL_SKIP_UUID,
                )
            else:
                skip_cursor, cursor_generated_at, cursor_snapshot_id = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT trial_balance_snapshot.trial_balance_snapshot_id,
                       trial_balance_snapshot.snapshot_generated_at,
                       trial_balance_snapshot.source_journal_count,
                       trial_balance_snapshot.source_payload_hash,
                       fiscal_period.period_code,
                       accounting_book_period_control.period_status_code,
                       accounting_book.book_name,
                       legal_entity_record.legal_entity_code
                FROM accounting_reporting.trial_balance_snapshot
                JOIN accounting_core.fiscal_period
                  ON fiscal_period.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND fiscal_period.fiscal_period_id = trial_balance_snapshot.fiscal_period_id
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND accounting_book.accounting_book_id = trial_balance_snapshot.accounting_book_id
                JOIN accounting_core.accounting_book_period_control
                  ON accounting_book_period_control.tenant_account_id
                     = trial_balance_snapshot.tenant_account_id
                 AND accounting_book_period_control.accounting_book_id
                     = trial_balance_snapshot.accounting_book_id
                 AND accounting_book_period_control.fiscal_period_id
                     = trial_balance_snapshot.fiscal_period_id
                JOIN accounting_core.legal_entity_record
                  ON legal_entity_record.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND legal_entity_record.legal_entity_id = trial_balance_snapshot.legal_entity_id
                WHERE trial_balance_snapshot.tenant_account_id = %s
                  AND trial_balance_snapshot.legal_entity_id = %s
                  AND (%s OR trial_balance_snapshot.fiscal_period_id = %s)
                  AND (%s OR accounting_book_period_control.period_status_code = %s)
                  AND (
                        %s
                        OR (
                              trial_balance_snapshot.snapshot_generated_at,
                              trial_balance_snapshot.trial_balance_snapshot_id
                           ) > (%s, %s)
                      )
                ORDER BY trial_balance_snapshot.snapshot_generated_at,
                         trial_balance_snapshot.trial_balance_snapshot_id
                LIMIT %s
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    skip_period,
                    period_id_value,
                    not period_status_code,
                    period_status_code,
                    skip_cursor,
                    cursor_generated_at,
                    cursor_snapshot_id,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            period_closes = [
                {
                    "tenant_reference": self._tenant_reference,
                    "legal_entity_reference": row[7],
                    "accounting_book_reference": row[6],
                    "period_code": row[4],
                    "period_status_code": row[5],
                    "snapshot_record_id": str(row[0]),
                    "snapshot_generated_at": _format_timestamp(row[1]),
                    "source_journal_count": int(row[2]),
                    "source_payload_hash": row[3],
                    "replayed": False,
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[1])}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "period_closes": period_closes,
                "next_cursor": next_cursor,
            }
            if period_code:
                document["fiscal_period_reference"] = (
                    f"urn:cwl:accounting:fiscal_period:{period_code}"
                )
            if period_status_code:
                document["period_status_code"] = period_status_code
            return document

    def load_unpublished_outbox_events(
        self,
        event_type_code: str,
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, UUID] | None = None,
    ) -> dict[str, object]:
        """Return one page of unpublished outbox rows for one tenant event type."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            if cursor_after is None:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    True,
                    _SQL_SKIP_DATETIME,
                    _SQL_SKIP_UUID,
                )
            else:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT outbox_event.outbox_event_id,
                       outbox_event.event_type_code,
                       outbox_event.aggregate_reference,
                       outbox_event.payload_reference,
                       outbox_event.payload_hash,
                       outbox_event.created_at
                FROM accounting_integration.outbox_event
                WHERE outbox_event.tenant_account_id = %s
                  AND outbox_event.event_type_code = %s
                  AND outbox_event.published_at IS NULL
                  AND (
                        %s
                        OR (outbox_event.created_at, outbox_event.outbox_event_id)
                           > (%s, %s)
                      )
                ORDER BY outbox_event.created_at, outbox_event.outbox_event_id
                LIMIT %s
                """,
                (
                    tenant_id,
                    event_type_code,
                    skip_cursor,
                    cursor_created_at,
                    cursor_event_id,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            events = [
                {
                    "outbox_event_id": str(row[0]),
                    "event_type_code": row[1],
                    "aggregate_reference": row[2],
                    "payload_reference": row[3],
                    "payload_hash": row[4],
                    "created_at": _format_timestamp(row[5]),
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[5])}|{last[0]}"
            return {
                "tenant_reference": self._tenant_reference,
                "event_type_code": event_type_code,
                "outbox_events": events,
                "next_cursor": next_cursor,
            }

    def load_audit_events(
        self,
        event_type_code: str = "",
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, UUID] | None = None,
    ) -> dict[str, object]:
        """Return one page of published and unpublished outbox rows for one tenant."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            if cursor_after is None:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    True,
                    _SQL_SKIP_DATETIME,
                    _SQL_SKIP_UUID,
                )
            else:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT outbox_event.outbox_event_id,
                       outbox_event.event_type_code,
                       outbox_event.aggregate_reference,
                       outbox_event.payload_reference,
                       outbox_event.payload_hash,
                       outbox_event.created_at,
                       outbox_event.published_at
                FROM accounting_integration.outbox_event
                WHERE outbox_event.tenant_account_id = %s
                  AND (%s OR outbox_event.event_type_code = %s)
                  AND (
                        %s
                        OR (outbox_event.created_at, outbox_event.outbox_event_id)
                           > (%s, %s)
                      )
                ORDER BY outbox_event.created_at, outbox_event.outbox_event_id
                LIMIT %s
                """,
                (
                    tenant_id,
                    not event_type_code,
                    event_type_code,
                    skip_cursor,
                    cursor_created_at,
                    cursor_event_id,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            events = [
                {
                    "outbox_event_id": str(row[0]),
                    "event_type_code": row[1],
                    "aggregate_reference": row[2],
                    "payload_reference": row[3],
                    "payload_hash": row[4],
                    "created_at": _format_timestamp(row[5]),
                    "published_at": (
                        None if row[6] is None else _format_timestamp(row[6])
                    ),
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[5])}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "audit_events": events,
                "next_cursor": next_cursor,
            }
            if event_type_code:
                document["event_type_code"] = event_type_code
            return document

    def publish_outbox_event(self, outbox_event_id: str) -> dict[str, object]:
        """Set published_at on one tenant outbox row, or replay an already-published row."""
        if not outbox_event_id:
            raise AccountingValidationError(
                "outbox_event_id is required. "
                "Supply the outbox event id, then retry the outbox publish."
            )
        try:
            event_id = UUID(outbox_event_id)
        except ValueError as error:
            raise AccountingValidationError(
                "outbox_event_id must be a UUID. "
                "Supply the outbox event id, then retry the outbox publish."
            ) from error
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            updated = connection.execute(
                """
                UPDATE accounting_integration.outbox_event
                SET published_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND outbox_event_id = %s
                  AND published_at IS NULL
                RETURNING outbox_event_id, event_type_code, aggregate_reference,
                          payload_reference, payload_hash, created_at, published_at
                """,
                (tenant_id, event_id),
            ).fetchone()
            row = updated
            if row is None:
                row = connection.execute(
                    """
                    SELECT outbox_event_id, event_type_code, aggregate_reference,
                           payload_reference, payload_hash, created_at, published_at
                    FROM accounting_integration.outbox_event
                    WHERE tenant_account_id = %s AND outbox_event_id = %s
                    """,
                    (tenant_id, event_id),
                ).fetchone()
            if row is None:
                raise AccountingValidationError(
                    "outbox event is missing for this outbox_event_id. "
                    "Accept the proposal, then retry the outbox publish."
                )
            return {
                "outbox_event_id": str(row[0]),
                "event_type_code": row[1],
                "aggregate_reference": row[2],
                "payload_reference": row[3],
                "payload_hash": row[4],
                "created_at": _format_timestamp(row[5]),
                "published_at": _format_timestamp(row[6]),
            }

    def _persist_proposal(
        self, proposal: JournalProposal, policy: AccountingPolicy | None
    ) -> PostingReceipt:
        """Resolve optional catalog policy and persist *proposal* in one transaction."""
        proposal_uuid = _require_proposal_uuid(proposal.proposal_id)
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(
                connection, f"proposal:{proposal.idempotency_key}"
            )
            prior = connection.execute(
                """
                SELECT source_payload_hash
                FROM accounting_integration.journal_proposal_record
                WHERE tenant_account_id = %s AND idempotency_key = %s
                """,
                (tenant_id, proposal.idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[0] != proposal.source_payload_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different payload"
                    )
                return self._receipt_for_idempotency_key(connection, tenant_id, proposal)
            if any(line.account_role_code == "retained_earnings" for line in proposal.lines):
                raise AccountingValidationError(
                    "retained_earnings is reserved for AIS period-close. "
                    "Post revenue and expense through Billing, then hard-close; "
                    "no journal was written."
                )
            if policy is None:
                policy = self._resolve_accounting_policy(connection, tenant_id, proposal)
            PostingLedger._validate_policy_scope(proposal, policy)
            resolved_lines = tuple(
                PostingLedger._resolve_line(line, policy) for line in proposal.lines
            )
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, proposal.legal_entity_reference
            )
            book_id = self._require_book(
                connection,
                tenant_id,
                legal_entity_id,
                policy.intended_book_role_code,
                policy.accounting_book_reference,
            )
            period_id = self._require_open_book_period(
                connection, tenant_id, book_id, proposal.accounting_date
            )
            journal_reference = f"urn:cwl:accounting:general_journal:{proposal.proposal_id}"
            receipt = PostingReceipt(
                receipt_reference=f"urn:cwl:accounting:posting_receipt:{proposal.proposal_id}",
                journal_reference=journal_reference,
                posting_status_code="posted",
                source_proposal_id=proposal.proposal_id,
                source_payload_hash=proposal.source_payload_hash,
                tenant_reference=proposal.tenant_reference,
                legal_entity_reference=proposal.legal_entity_reference,
                accounting_book_reference=policy.accounting_book_reference,
                accounting_policy_version=policy.accounting_policy_version,
                posting_rule_version=policy.posting_rule_version,
                line_count=len(resolved_lines),
            )
            proposal_record_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, %s, %s, %s, %s, 'posted', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (
                    tenant_id,
                    proposal_uuid,
                    proposal.proposal_contract_version,
                    proposal.idempotency_key,
                    proposal.source_payload_hash,
                ),
            ).fetchone()[0]
            journal_id = self._insert_journal(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
                journal_reference=journal_reference,
                proposal=proposal,
                policy=policy,
                proposal_record_id=proposal_record_id,
                lines=resolved_lines,
            )
            self._insert_receipt(
                connection, tenant_id, proposal_record_id, journal_id, receipt
            )
            self._insert_outbox(
                connection,
                tenant_id,
                "posting_receipt",
                journal_reference,
                receipt.receipt_reference,
                receipt,
            )
            return receipt

    def close_fiscal_period(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        snapshot_currency_code: str,
        period_status_code: str = "hard_closed",
        idempotency_key: str = "",
    ) -> PeriodCloseReceipt:
        """Soft-close or hard-close one fiscal period; only hard-close snapshots the book."""
        _require_reference(legal_entity_reference, "legal entity reference")
        _require_reference(accounting_book_reference, "accounting book reference")
        if not period_code.strip():
            raise AccountingValidationError(
                "period_code is required. Supply the fiscal period code, then retry the close."
            )
        close_idempotency_key = idempotency_key.strip() or (
            f"{self._tenant_reference}:period_close:{accounting_book_reference}:{period_code}"
        )
        try:
            _require_currency(snapshot_currency_code)
        except AccountingValidationError as error:
            raise AccountingValidationError(
                "snapshot_currency_code must be a three-letter ISO currency. "
                "Supply the book reporting currency, then retry the close."
            ) from error
        if period_status_code not in {"soft_closed", "hard_closed"}:
            raise AccountingValidationError(
                "period_status_code must be soft_closed or hard_closed. "
                "Supply one of those codes, then retry the close."
            )
        with self._session() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self._active_connection = connection
            try:
                tenant_id = self._require_tenant(connection)
                legal_entity_id = self._require_legal_entity(
                    connection,
                    tenant_id,
                    legal_entity_reference,
                    next_action="the close",
                )
                book_id, reporting_currency_code = self._require_book_for_close(
                    connection, tenant_id, legal_entity_id, accounting_book_reference
                )
                self._acquire_command_lock(
                    connection, f"period:{book_id}:{period_code}"
                )
                if snapshot_currency_code != reporting_currency_code:
                    raise AccountingValidationError(
                        f"snapshot currency {snapshot_currency_code} does not match book reporting "
                        f"currency {reporting_currency_code}. Supply the book reporting currency, "
                        "then retry the close."
                    )
                period_id, current_status, period_end_date = self._lock_book_period(
                    connection, tenant_id, book_id, period_code
                )
                if current_status == "hard_closed":
                    if period_status_code == "soft_closed":
                        raise AccountingValidationError(
                            f"Fiscal period {period_code} is hard_closed. "
                            "Hard-closed periods cannot be soft-closed. "
                            "Open a later period or leave this period hard_closed; "
                            "no close row was written."
                        )
                    return self._replay_close_receipt(
                        connection,
                        tenant_id=tenant_id,
                        legal_entity_id=legal_entity_id,
                        book_id=book_id,
                        period_id=period_id,
                        period_code=period_code,
                        current_status=current_status,
                        legal_entity_reference=legal_entity_reference,
                        accounting_book_reference=accounting_book_reference,
                        idempotency_key=close_idempotency_key,
                    )
                if current_status == period_status_code:
                    return self._replay_soft_close_receipt(
                        connection,
                        tenant_id=tenant_id,
                        legal_entity_id=legal_entity_id,
                        book_id=book_id,
                        period_id=period_id,
                        period_code=period_code,
                        period_end_date=period_end_date,
                        snapshot_currency_code=snapshot_currency_code,
                        legal_entity_reference=legal_entity_reference,
                        accounting_book_reference=accounting_book_reference,
                        idempotency_key=close_idempotency_key,
                    )
                if period_status_code == "soft_closed":
                    return self._persist_soft_close(
                        connection,
                        tenant_id=tenant_id,
                        legal_entity_id=legal_entity_id,
                        book_id=book_id,
                        period_id=period_id,
                        period_code=period_code,
                        period_end_date=period_end_date,
                        snapshot_currency_code=snapshot_currency_code,
                        legal_entity_reference=legal_entity_reference,
                        accounting_book_reference=accounting_book_reference,
                        idempotency_key=close_idempotency_key,
                    )
                package = self._assemble_period_close_package(
                    legal_entity_reference,
                    accounting_book_reference,
                    period_code,
                )
                self._require_closeable_package(package)
                return self._persist_period_close(
                    connection,
                    tenant_id=tenant_id,
                    legal_entity_id=legal_entity_id,
                    book_id=book_id,
                    period_id=period_id,
                    period_code=period_code,
                    period_end_date=period_end_date,
                    period_status_code=period_status_code,
                    snapshot_currency_code=snapshot_currency_code,
                    legal_entity_reference=legal_entity_reference,
                    accounting_book_reference=accounting_book_reference,
                    idempotency_key=close_idempotency_key,
                )
            finally:
                self._active_connection = None

    def open_fiscal_period(
        self,
        legal_entity_reference: str,
        period_code: str,
        period_start_date: date | None = None,
        period_end_date: date | None = None,
        *,
        idempotency_key: str,
        source_payload_hash: str,
    ) -> dict[str, object]:
        """Insert or replay one fiscal-period-open command from durable evidence."""
        if not legal_entity_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference and fiscal_period_reference are required. "
                "Supply those period-open fields, then retry the period open."
            )
        command_key = idempotency_key.strip()
        if not command_key or command_key != idempotency_key:
            raise AccountingValidationError(
                "period-open idempotency_key must be a canonical non-empty string. "
                "Supply the original command key, then retry the period open."
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", source_payload_hash) is None:
            raise AccountingValidationError(
                "period-open source_payload_hash must be a canonical sha256 digest. "
                "Supply the immutable command hash, then retry the period open."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(connection, f"period-open:{command_key}")
            self._acquire_command_lock(connection, f"period:{period_code}")
            legal_entity_id, _functional_currency = self._load_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the period open",
            )
            prior = connection.execute(
                """
                SELECT period_open_command.legal_entity_id,
                       fiscal_period.period_code,
                       period_open_command.requested_period_start_date,
                       period_open_command.requested_period_end_date,
                       fiscal_period.period_start_date,
                       fiscal_period.period_end_date,
                       period_open_command.source_payload_hash
                FROM accounting_integration.fiscal_period_open_command AS period_open_command
                JOIN accounting_core.fiscal_period AS fiscal_period
                  ON fiscal_period.tenant_account_id = period_open_command.tenant_account_id
                 AND fiscal_period.fiscal_period_id = period_open_command.fiscal_period_id
                WHERE period_open_command.tenant_account_id = %s
                  AND period_open_command.period_open_idempotency_key = %s
                """,
                (tenant_id, command_key),
            ).fetchone()
            if prior is not None:
                (
                    prior_legal_entity_id,
                    prior_period_code,
                    prior_requested_start,
                    prior_requested_end,
                    stored_start_date,
                    stored_end_date,
                    prior_source_hash,
                ) = prior
                if (
                    prior_legal_entity_id != legal_entity_id
                    or prior_period_code != period_code
                    or prior_requested_start != period_start_date
                    or prior_requested_end != period_end_date
                    or prior_source_hash != source_payload_hash
                ):
                    raise IdempotencyConflictError(
                        "period-open idempotency key was already used with a different payload"
                    )
                return self._period_open_document(
                    legal_entity_reference,
                    period_code,
                    stored_start_date,
                    stored_end_date,
                    replayed=True,
                )

            existing = self._load_period_state(connection, tenant_id, period_code)
            replayed = existing is not None
            if existing is not None:
                period_id, current_status, stored_start_date, stored_end_date = existing
                if current_status != "open":
                    raise AccountingValidationError(
                        f"Fiscal period {period_code} is {current_status}. "
                        "Closed periods cannot be reopened. Open a later period, "
                        "then retry the period open."
                    )
                if (
                    period_start_date is not None
                    and period_start_date != stored_start_date
                ) or (
                    period_end_date is not None and period_end_date != stored_end_date
                ):
                    raise AccountingValidationError(
                        "period-open dates do not match the already-open fiscal period. "
                        "Supply its existing dates or omit both dates, then retry."
                    )
            else:
                if period_start_date is None or period_end_date is None:
                    raise AccountingValidationError(
                        "period_start_date and period_end_date are required. "
                        "Supply those fiscal_period dates, then retry the period open."
                    )
                if period_end_date < period_start_date:
                    raise AccountingValidationError(
                        "period_end_date must be on or after period_start_date. "
                        "Supply a valid date range, then retry the period open."
                    )
                calendar_id = self._require_tenant_calendar(connection, tenant_id)
                period_id = connection.execute(
                    """
                    INSERT INTO accounting_core.fiscal_period (
                        tenant_account_id, fiscal_calendar_id, period_code,
                        period_start_date, period_end_date, period_status_code
                    )
                    VALUES (%s, %s, %s, %s, %s, 'open')
                    RETURNING fiscal_period_id
                    """,
                    (
                        tenant_id,
                        calendar_id,
                        period_code,
                        period_start_date,
                        period_end_date,
                    ),
                ).fetchone()[0]
                stored_start_date = period_start_date
                stored_end_date = period_end_date

            connection.execute(
                """
                INSERT INTO accounting_integration.fiscal_period_open_command (
                    tenant_account_id,
                    legal_entity_id,
                    fiscal_period_id,
                    period_open_idempotency_key,
                    source_payload_hash,
                    requested_period_start_date,
                    requested_period_end_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    period_id,
                    command_key,
                    source_payload_hash,
                    period_start_date,
                    period_end_date,
                ),
            )
            return self._period_open_document(
                legal_entity_reference,
                period_code,
                stored_start_date,
                stored_end_date,
                replayed=replayed,
            )

    def load_fiscal_period(
        self, legal_entity_reference: str, period_code: str
    ) -> dict[str, object]:
        """Return persisted fiscal-period status and dates for one tenant entity."""
        if not legal_entity_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference and fiscal_period_reference are required. "
                "Supply those period fields, then retry the period read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the period read"
            )
            existing = self._load_period_state(connection, tenant_id, period_code)
            if existing is None:
                raise AccountingValidationError(
                    f"Fiscal period {period_code} is not recorded for this tenant. "
                    "Create the fiscal_period row, then retry the period read."
                )
            _period_id, current_status, start_date, end_date = existing
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
                "period_code": period_code,
                "period_status_code": current_status,
                "period_start_date": start_date.isoformat(),
                "period_end_date": end_date.isoformat(),
            }

    def load_fiscal_periods(
        self,
        legal_entity_reference: str,
        *,
        page_limit: int = 50,
        cursor_after: tuple[date, str] | None = None,
    ) -> dict[str, object]:
        """Return one page of existing fiscal periods for a tenant legal entity."""
        if not legal_entity_reference:
            raise AccountingValidationError(
                "legal_entity_reference is required. "
                "Supply that period-list field, then retry the period list."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the period list"
            )
            calendar_row = connection.execute(
                """
                SELECT fiscal_calendar_id
                FROM accounting_core.fiscal_calendar
                WHERE tenant_account_id = %s
                ORDER BY calendar_code
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()
            periods: list[dict[str, object]] = []
            next_cursor = None
            if calendar_row is not None:
                if cursor_after is None:
                    skip_cursor, cursor_start_date, cursor_period_code = (
                        True,
                        _SQL_SKIP_DATE,
                        "",
                    )
                else:
                    skip_cursor, cursor_start_date, cursor_period_code = (
                        False,
                        cursor_after[0],
                        cursor_after[1],
                    )
                rows = connection.execute(
                    """
                    SELECT fiscal_period.period_code,
                           fiscal_period.period_start_date,
                           fiscal_period.period_end_date,
                           fiscal_period.period_status_code
                    FROM accounting_core.fiscal_period
                    WHERE fiscal_period.tenant_account_id = %s
                      AND fiscal_period.fiscal_calendar_id = %s
                      AND (
                            %s
                            OR (fiscal_period.period_start_date, fiscal_period.period_code)
                               > (%s, %s)
                          )
                    ORDER BY fiscal_period.period_start_date, fiscal_period.period_code
                    LIMIT %s
                    """,
                    (
                        tenant_id,
                        calendar_row[0],
                        skip_cursor,
                        cursor_start_date,
                        cursor_period_code,
                        page_limit + 1,
                    ),
                ).fetchall()
                has_more = len(rows) > page_limit
                page_rows = rows[:page_limit]
                periods = [
                    {
                        "fiscal_period_reference": (
                            f"urn:cwl:accounting:fiscal_period:{row[0]}"
                        ),
                        "period_code": row[0],
                        "period_start_date": row[1].isoformat(),
                        "period_end_date": row[2].isoformat(),
                        "period_status_code": row[3],
                    }
                    for row in page_rows
                ]
                if has_more:
                    last = page_rows[-1]
                    next_cursor = f"{last[1].isoformat()}|{last[0]}"
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "fiscal_periods": periods,
                "next_cursor": next_cursor,
            }

    # The remaining reporting, statement, reconciliation, and persistence methods
    # are unchanged from the immediately preceding exact head. This update only
    # removes application-side book-period authority synthesis/fallback in the
    # three authority helpers below.

    def _require_open_book_period(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        accounting_date: date,
    ) -> UUID:
        """Require an open fiscal period for the selected accounting book."""
        return self._require_open_book_period_bounds(
            connection, tenant_id, book_id, accounting_date
        )[0]

    def _require_open_book_period_bounds(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        accounting_date: date,
    ) -> tuple[UUID, date, date]:
        """Return period identity and bounds when this accounting book is open."""
        period_row = connection.execute(
            """
            SELECT fiscal_period_id, period_code,
                   period_start_date, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s
              AND period_start_date <= %s
              AND period_end_date >= %s
            """,
            (tenant_id, accounting_date, accounting_date),
        ).fetchone()
        if period_row is None:
            raise AccountingValidationError(
                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "
                "Create an open fiscal period on the tenant calendar, then retry posting."
            )
        period_id, period_code, period_start_date, period_end_date = period_row
        control_row = connection.execute(
            """
            SELECT accounting_book_period_control.period_status_code
            FROM accounting_core.fiscal_period
            JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = fiscal_period.tenant_account_id
             AND accounting_book_period_control.fiscal_period_id
                 = fiscal_period.fiscal_period_id
             AND accounting_book_period_control.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.fiscal_period_id = %s
            """,
            (book_id, tenant_id, period_id),
        ).fetchone()
        if control_row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} has no control row for this accounting book. "
                "Repair the fiscal-period control data for this book, then retry posting."
            )
        period_status_code = control_row[0]
        if period_status_code != "open":
            locked_marker = " (period_closed)" if period_status_code == "hard_closed" else ""
            raise AccountingValidationError(
                f"Fiscal period {period_code} is {period_status_code}{locked_marker}. "
                "Open that period or post into an open period for this accounting book; "
                "no journal was written."
            )
        return period_id, period_start_date, period_end_date

    def _require_adjusting_period(
        self, connection: object, tenant_id: UUID, accounting_date: date
    ) -> UUID:
        return self._require_adjusting_period_bounds(connection, tenant_id, accounting_date)[0]

    def _require_adjusting_period_bounds(
        self, connection: object, tenant_id: UUID, accounting_date: date
    ) -> tuple[UUID, date, date]:
        return self._require_period_bounds(
            connection,
            tenant_id,
            accounting_date,
            allowed_status_codes=frozenset({"open", "soft_closed"}),
            next_action="Reverse into an open or soft-closed period",
        )

    def _require_period_bounds(
        self,
        connection: object,
        tenant_id: UUID,
        accounting_date: date,
        *,
        allowed_status_codes: frozenset[str],
        next_action: str,
    ) -> tuple[UUID, date, date]:
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_code, period_status_code,
                   period_start_date, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s
              AND period_start_date <= %s
              AND period_end_date >= %s
            """,
            (tenant_id, accounting_date, accounting_date),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "
                "Create an open fiscal period on the tenant calendar, then retry posting."
            )
        period_id, period_code = row[0], row[1]
        self._acquire_command_lock(connection, f"period:{period_code}")
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_code, period_status_code,
                   period_start_date, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s
              AND fiscal_period_id = %s
            """,
            (tenant_id, period_id),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "
                "Create an open fiscal period on the tenant calendar, then retry posting."
            )
        if row[2] not in allowed_status_codes:
            locked_marker = " (period_closed)" if row[2] == "hard_closed" else ""
            raise AccountingValidationError(
                f"Fiscal period {row[1]} is {row[2]}{locked_marker}. {next_action}; "
                "no journal was written."
            )
        return row[0], row[3], row[4]

    def _require_fiscal_period(
        self,
        connection: object,
        tenant_id: UUID,
        period_code: str,
        next_action: str = "the close",
    ) -> tuple[UUID, str, date]:
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_status_code, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            """,
            (tenant_id, period_code),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is not recorded for this tenant. "
                f"Create the fiscal_period row, then retry {next_action}."
            )
        return row[0], row[1], row[2]

    def _lock_book_period(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        period_code: str,
    ) -> tuple[UUID, str, date]:
        """Lock authoritative close state for one accounting book."""
        period_row = connection.execute(
            """
            SELECT fiscal_period_id
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            """,
            (tenant_id, period_code),
        ).fetchone()
        if period_row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is not recorded for this tenant. "
                "Create the fiscal_period row, then retry the close."
            )
        period_id = period_row[0]
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   accounting_book_period_control.period_status_code,
                   fiscal_period.period_end_date
            FROM accounting_core.fiscal_period
            JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = fiscal_period.tenant_account_id
             AND accounting_book_period_control.fiscal_period_id
                 = fiscal_period.fiscal_period_id
             AND accounting_book_period_control.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.fiscal_period_id = %s
            FOR UPDATE OF accounting_book_period_control
            """,
            (book_id, tenant_id, period_id),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} has no control row for this accounting book. "
                "Repair the fiscal-period control data for this book, then retry the close."
            )
        return row[0], row[1], row[2]

    def _load_book_period_state(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        period_code: str,
    ) -> tuple[UUID, str, date, date] | None:
        """Return the selected book's authoritative period state."""
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   accounting_book_period_control.period_status_code,
                   fiscal_period.period_start_date,
                   fiscal_period.period_end_date
            FROM accounting_core.fiscal_period
            JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = fiscal_period.tenant_account_id
             AND accounting_book_period_control.fiscal_period_id
                 = fiscal_period.fiscal_period_id
             AND accounting_book_period_control.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.period_code = %s
            """,
            (book_id, tenant_id, period_code),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], row[2], row[3]

    def _load_period_state(
        self, connection: object, tenant_id: UUID, period_code: str
    ) -> tuple[UUID, str, date, date] | None:
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_status_code, period_start_date, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            """,
            (tenant_id, period_code),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], row[2], row[3]


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the checked-in PostgreSQL 18 accounting foundation in migration order."""
    from .migration_install import apply_foundation_migration as _install

    _install(database_url, migration_path)


def _import_psycopg():
    try:
        return importlib.import_module("psycopg")
    except ImportError as error:
        raise AccountingValidationError(
            "the accounting database adapter is unavailable on this deployment. "
            "Ask the platform operator to install the pinned runtime dependencies, "
            "then retry the request."
        ) from error


def _require_proposal_uuid(proposal_id: str) -> UUID:
    return uuid.UUID(_require_proposal_id(proposal_id))


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _vat_register_is_loadable(register_document: dict[str, object]) -> bool:
    return {
        "tenant_reference",
        "legal_entity_reference",
        "accounting_book_reference",
        "book_reference",
        "fiscal_period_reference",
        "as_of_date",
        "chart_account_code",
        "account_role_code",
        "issued_amount",
        "voided_amount",
        "closing_amount",
    }.issubset(register_document.keys())


def _fiscal_year_identity(period_code: str, period_start_date: date | None) -> str:
    matched = re.match(r"^(\d{4})", period_code)
    if matched:
        return matched.group(1)
    if period_start_date is not None:
        return f"{period_start_date.year:04d}"
    raise AccountingValidationError(
        "fiscal year identity is missing for this period. "
        "Use a period_code that starts with the four-digit year, then retry the financial-statement read."
    )
