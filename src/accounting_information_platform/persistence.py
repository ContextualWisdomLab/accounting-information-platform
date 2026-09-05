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
                    f"Fiscal period {period_code} has no authoritative control row for this accounting book. "
                    "Create or repair the book-period control through the canonical period lifecycle, "
                    "then retry the journal post."
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

    def _require_open_book_period_bounds(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        accounting_date: date,
    ) -> tuple[UUID, date, date]:
        """Return period identity and bounds when this accounting book is authoritatively open."""
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   fiscal_period.period_code,
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
              AND fiscal_period.period_start_date <= %s
              AND fiscal_period.period_end_date >= %s
            """,
            (book_id, tenant_id, accounting_date, accounting_date),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No authoritative open book-period covers accounting date {accounting_date.isoformat()}. "
                "Create the fiscal period and its book-period control through the canonical lifecycle, "
                "then retry posting."
            )
        period_id = row[0]
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   fiscal_period.period_code,
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
              AND fiscal_period.fiscal_period_id = %s
            """,
            (book_id, tenant_id, period_id),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No authoritative open book-period covers accounting date {accounting_date.isoformat()}. "
                "Create the fiscal period and its book-period control through the canonical lifecycle, "
                "then retry posting."
            )
        if row[2] != "open":
            locked_marker = " (period_closed)" if row[2] == "hard_closed" else ""
            raise AccountingValidationError(
                f"Fiscal period {row[1]} is {row[2]}{locked_marker}. "
                "Open that period or post into an open period for this accounting book; "
                "no journal was written."
            )
        return row[0], row[3], row[4]

    def _lock_book_period(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        period_code: str,
    ) -> tuple[UUID, str, date]:
        """Lock existing authoritative close state for one accounting book."""
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
        """Return the selected book's authoritative period-control state."""
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


# NOTE: Remaining production methods are intentionally omitted from this replacement.
