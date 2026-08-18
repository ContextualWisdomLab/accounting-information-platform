"""Thin stdlib HTTP boundary for Billing proposals, AIS adjusting journals, pulls, receipts, close, open, TB, statements, catalog, ledgers, balances, rollforwards, journals, reversals, outbox, and audit history."""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .accept import (
    accept_adjusting_journal,
    accept_journal_proposal,
    accept_journal_reversal,
    accept_period_close,
    accept_period_open,
    lookup_account_balances,
    lookup_account_rollforward,
    lookup_account_ledger,
    lookup_account_role_mappings,
    lookup_accounting_books,
    lookup_chart_accounts,
    lookup_audit_events,
    lookup_legal_entities,
    lookup_financial_statement,
    lookup_fiscal_period,
    lookup_fiscal_periods,
    lookup_journal_reversals,
    lookup_period_closes,
    lookup_outbox_events,
    lookup_period_journals,
    lookup_posted_journal,
    lookup_published_receipt,
    lookup_trial_balance,
    publish_outbox_event,
)
from .billing_pull import accept_billing_proposal_pull
from .core import AccountingValidationError, IdempotencyConflictError, _require_reference


TENANT_HEADER = "X-CWL-Tenant-Reference"
HEALTHZ_PATH = "/healthz"
BILLING_PROPOSAL_PULL_PATH = "/billing-proposal-pulls"
JOURNAL_PROPOSAL_PATH = "/journal-proposals"
JOURNAL_REVERSAL_PATH = "/journal-reversals"
PERIOD_CLOSE_PATH = "/period-closes"
POSTING_RECEIPT_PATH = "/posting-receipts"
TRIAL_BALANCE_PATH = "/trial-balances"
FINANCIAL_STATEMENT_PATH = "/financial-statements"
ACCOUNT_ROLE_MAPPING_PATH = "/account-role-mappings"
ACCOUNTING_BOOK_PATH = "/accounting-books"
LEGAL_ENTITY_PATH = "/legal-entities"
CHART_ACCOUNT_PATH = "/chart-accounts"
ACCOUNT_LEDGER_PATH = "/account-ledgers"
ACCOUNT_BALANCE_PATH = "/account-balances"
ACCOUNT_ROLLFORWARD_PATH = "/account-rollforwards"
JOURNAL_PATH = "/journals"
FISCAL_PERIOD_PATH = "/fiscal-periods"
OUTBOX_PATH = "/outbox-events"
AUDIT_EVENT_PATH = "/audit-events"
_OUTBOX_PUBLISH_PATH = re.compile(
    r"^/outbox-events/"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"/publish$"
)


class JournalProposalServer(ThreadingHTTPServer):
    """HTTP server bound to one AIS tenant and PostgreSQL URL."""

    def __init__(
        self,
        server_address: tuple[str, int],
        database_url: str,
        tenant_reference: str,
    ) -> None:
        """Bind *server_address* to one tenant's posting endpoint."""
        self.database_url = database_url
        self.tenant_reference = tenant_reference
        super().__init__(server_address, JournalProposalHandler)


class JournalProposalHandler(BaseHTTPRequestHandler):
    """Serve proposal POST, reverse, reversal list, pull, receipt GET, close, TB, catalog, journal, outbox, audit history, and healthz."""

    server: JournalProposalServer

    def do_GET(self) -> None:
        """Route healthz, receipt, trial-balance, catalog, journal, reversal list, close list, outbox, audit history, and GET 405s."""
        parsed = urlparse(self.path)
        if parsed.path == HEALTHZ_PATH:
            self._write_json(200, {"status": "ok"})
            return
        if parsed.path == BILLING_PROPOSAL_PULL_PATH:
            self._write_error(
                405,
                "GET is not supported on the billing proposal pull endpoint. "
                "POST a billing-proposal-pull command, then retry.",
            )
            return
        if parsed.path == JOURNAL_REVERSAL_PATH:
            self._get_journal_reversals(parsed.query)
            return
        if parsed.path == JOURNAL_PROPOSAL_PATH:
            self._write_error(
                405,
                "GET is not supported on the journal proposal endpoint. "
                "POST a Billing accounting_journal_proposal, then retry.",
            )
            return
        if parsed.path == POSTING_RECEIPT_PATH:
            self._get_posting_receipt(parsed.query)
            return
        if parsed.path == TRIAL_BALANCE_PATH:
            self._get_trial_balance(parsed.query)
            return
        if parsed.path == FINANCIAL_STATEMENT_PATH:
            self._get_financial_statement(parsed.query)
            return
        if parsed.path == ACCOUNT_ROLE_MAPPING_PATH:
            self._get_account_role_mappings(parsed.query)
            return
        if parsed.path == ACCOUNTING_BOOK_PATH:
            self._get_accounting_books(parsed.query)
            return
        if parsed.path == LEGAL_ENTITY_PATH:
            self._get_legal_entities()
            return
        if parsed.path == CHART_ACCOUNT_PATH:
            self._get_chart_accounts(parsed.query)
            return
        if parsed.path == ACCOUNT_LEDGER_PATH:
            self._get_account_ledger(parsed.query)
            return
        if parsed.path == ACCOUNT_BALANCE_PATH:
            self._get_account_balances(parsed.query)
            return
        if parsed.path == ACCOUNT_ROLLFORWARD_PATH:
            self._get_account_rollforward(parsed.query)
            return
        if parsed.path == JOURNAL_PATH:
            self._get_posted_journal(parsed.query)
            return
        if parsed.path == PERIOD_CLOSE_PATH:
            self._get_period_closes(parsed.query)
            return
        if parsed.path == FISCAL_PERIOD_PATH:
            self._get_fiscal_period(parsed.query)
            return
        if parsed.path == OUTBOX_PATH:
            self._get_outbox_events(parsed.query)
            return
        if parsed.path == AUDIT_EVENT_PATH:
            self._get_audit_events(parsed.query)
            return
        if _OUTBOX_PUBLISH_PATH.fullmatch(parsed.path):
            self._write_error(
                405,
                "GET is not supported on the outbox publish endpoint. "
                "POST the outbox publish, then retry.",
            )
            return
        self._write_error(
            404,
            "unknown path. GET /posting-receipts?idempotency_key=, GET /trial-balances, "
            "GET /financial-statements, GET /account-role-mappings, GET /accounting-books, "
            "GET /legal-entities, GET /chart-accounts, GET /account-ledgers, "
            "GET /account-balances, GET /account-rollforwards, GET /journals, "
            "GET /journal-reversals, GET /period-closes, GET /fiscal-periods, "
            "GET /outbox-events?event_type_code=, or GET /audit-events, then retry.",
        )

    def do_POST(self) -> None:
        """Route journal-proposal accept, adjusting journal, reverse, Billing pull, close, outbox publish, audit-history 405, and GET-only POST 405s."""
        raw_body = self._read_body()
        parsed_path = urlparse(self.path).path
        if parsed_path == JOURNAL_PATH:
            self._post_adjusting_journal(raw_body)
            return
        if parsed_path == ACCOUNT_ROLE_MAPPING_PATH:
            self._write_error(
                405,
                "POST is not supported on the account role mapping endpoint. "
                "GET the catalog mappings, then retry.",
            )
            return
        if parsed_path == ACCOUNTING_BOOK_PATH:
            self._write_error(
                405,
                "POST is not supported on the accounting book catalog endpoint. "
                "GET the accounting books, then retry.",
            )
            return
        if parsed_path == LEGAL_ENTITY_PATH:
            self._write_error(
                405,
                "POST is not supported on the legal entity catalog endpoint. "
                "GET the legal entities, then retry.",
            )
            return
        if parsed_path == FINANCIAL_STATEMENT_PATH:
            self._write_error(
                405,
                "POST is not supported on the financial statement endpoint. "
                "GET the income statement or balance sheet, then retry.",
            )
            return
        if parsed_path == CHART_ACCOUNT_PATH:
            self._write_error(
                405,
                "POST is not supported on the chart account catalog endpoint. "
                "GET the chart accounts, then retry.",
            )
            return
        if parsed_path == ACCOUNT_LEDGER_PATH:
            self._write_error(
                405,
                "POST is not supported on the account ledger endpoint. "
                "GET the account ledger, then retry.",
            )
            return
        if parsed_path == ACCOUNT_BALANCE_PATH:
            self._write_error(
                405,
                "POST is not supported on the account balance endpoint. "
                "GET the account balances, then retry.",
            )
            return
        if parsed_path == ACCOUNT_ROLLFORWARD_PATH:
            self._write_error(
                405,
                "POST is not supported on the account rollforward endpoint. "
                "GET the account rollforward, then retry.",
            )
            return
        if self.path == JOURNAL_PROPOSAL_PATH:
            self._post_journal_proposal(raw_body)
            return
        if parsed_path == JOURNAL_REVERSAL_PATH:
            self._post_journal_reversal(raw_body)
            return
        if parsed_path == BILLING_PROPOSAL_PULL_PATH:
            self._post_billing_proposal_pull(raw_body)
            return
        if parsed_path == PERIOD_CLOSE_PATH:
            self._post_period_close(raw_body)
            return
        if parsed_path == FISCAL_PERIOD_PATH:
            self._post_fiscal_period(raw_body)
            return
        if parsed_path == OUTBOX_PATH:
            self._write_error(
                405,
                "POST is not supported on the outbox-event list endpoint. "
                "GET unpublished outbox events, then retry.",
            )
            return
        if parsed_path == AUDIT_EVENT_PATH:
            self._write_error(
                405,
                "POST is not supported on the audit-event history endpoint. "
                "GET the audit-event history, then retry. Drain unpublished rows with "
                "GET /outbox-events then POST /outbox-events/{outbox_event_id}/publish.",
            )
            return
        publish_match = _OUTBOX_PUBLISH_PATH.fullmatch(parsed_path)
        if publish_match:
            self._post_outbox_publish(publish_match.group(1))
            return
        self._write_error(
            404,
            "unknown path. POST /journal-proposals, POST /journals, POST /journal-reversals, "
            "POST /billing-proposal-pulls, POST /period-closes, POST /fiscal-periods, "
            "or POST /outbox-events/{outbox_event_id}/publish, then retry.",
        )

    def log_message(self, format: str, *args: object) -> None:
        """Omit request logs so receipts and tenant URNs are not written to stdout."""
        return

    def _get_posting_receipt(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("lookup")
        if tenant_header is None:
            return
        keys = parse_qs(query).get("idempotency_key", [])
        idempotency_key = keys[0] if keys else ""
        if not idempotency_key:
            self._write_error(
                400,
                "idempotency_key is required. "
                "Supply the Billing idempotency key, then retry the receipt read.",
            )
            return
        try:
            document = lookup_published_receipt(
                self.server.database_url, tenant_header, idempotency_key
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(200, document)

    def _get_trial_balance(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("trial-balance read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        if not legal_entity_reference or not book_reference or not fiscal_period_reference:
            self._write_error(
                400,
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those trial-balance fields, then retry the trial-balance read.",
            )
            return
        try:
            document = lookup_trial_balance(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(200, document)

    def _get_financial_statement(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("financial-statement read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        statement_type_code = _first_query(fields, "statement_type_code")
        comparison_fiscal_period_reference = _first_query(
            fields, "comparison_fiscal_period_reference"
        )
        statement_scope_code = _first_query(fields, "statement_scope_code")
        if (
            not legal_entity_reference
            or not book_reference
            or not fiscal_period_reference
            or not statement_type_code
        ):
            self._write_error(
                400,
                "legal_entity_reference, book_reference, fiscal_period_reference, "
                "and statement_type_code are required. "
                "Supply those financial-statement fields, then retry the financial-statement read.",
            )
            return
        try:
            document = lookup_financial_statement(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
                statement_type_code,
                comparison_fiscal_period_reference,
                statement_scope_code,
            )
        except AccountingValidationError as error:
            message = str(error)
            if "statement_type_code" in message or "statement_scope_code" in message:
                self._write_error(400, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_account_role_mappings(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("mapping read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        if not book_reference:
            book_reference = _first_query(fields, "accounting_book_reference")
        if not legal_entity_reference or not book_reference:
            self._write_error(
                400,
                "legal_entity_reference and book_reference are required. "
                "Supply those catalog fields, then retry the mapping read.",
            )
            return
        try:
            document = lookup_account_role_mappings(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(200, document)

    def _get_accounting_books(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("accounting-book list")
        if tenant_header is None:
            return
        legal_entity_reference = _first_query(parse_qs(query), "legal_entity_reference")
        if not legal_entity_reference:
            self._write_error(
                400,
                "legal_entity_reference is required. "
                "Supply that catalog field, then retry the accounting-book list.",
            )
            return
        try:
            document = lookup_accounting_books(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(200, document)

    def _get_legal_entities(self) -> None:
        tenant_header = self._bound_tenant_header("legal-entity list")
        if tenant_header is None:
            return
        try:
            document = lookup_legal_entities(
                self.server.database_url,
                tenant_header,
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(200, document)

    def _get_chart_accounts(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("chart-account read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        if not book_reference:
            book_reference = _first_query(fields, "accounting_book_reference")
        if not legal_entity_reference or not book_reference:
            self._write_error(
                400,
                "legal_entity_reference and book_reference are required. "
                "Supply those catalog fields, then retry the chart-account read.",
            )
            return
        try:
            document = lookup_chart_accounts(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(200, document)

    def _get_account_rollforward(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("account-rollforward read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        chart_account_code = _first_query(fields, "chart_account_code")
        statement_scope_code = _first_query(fields, "statement_scope_code")
        if (
            not legal_entity_reference
            or not book_reference
            or not fiscal_period_reference
            or not chart_account_code
        ):
            self._write_error(
                400,
                "legal_entity_reference, book_reference, fiscal_period_reference, "
                "and chart_account_code are required. "
                "Supply those account-rollforward fields, then retry the account-rollforward read.",
            )
            return
        try:
            document = lookup_account_rollforward(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
                chart_account_code,
                statement_scope_code,
            )
        except AccountingValidationError as error:
            message = str(error)
            if "statement_scope_code" in message:
                self._write_error(400, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_account_balances(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("account-balance read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        if not legal_entity_reference or not book_reference or not fiscal_period_reference:
            self._write_error(
                400,
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those account-balance fields, then retry the account-balance read.",
            )
            return
        raw_limit = _first_query(fields, "page_limit")
        page_limit: int | None = None
        if raw_limit:
            try:
                page_limit = int(raw_limit)
            except ValueError:
                self._write_error(
                    400,
                    "page_limit must be an integer. "
                    "Supply an account-balance page_limit, then retry the account-balance read.",
                )
                return
        try:
            document = lookup_account_balances(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
                chart_account_code=_first_query(fields, "chart_account_code"),
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor"),
            )
        except AccountingValidationError as error:
            message = str(error)
            if "page_limit" in message:
                self._write_error(400, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_account_ledger(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("account-ledger read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        chart_account_code = _first_query(fields, "chart_account_code")
        if not legal_entity_reference or not chart_account_code:
            self._write_error(
                400,
                "legal_entity_reference and chart_account_code are required. "
                "Supply those ledger fields, then retry the account-ledger read.",
            )
            return
        raw_limit = _first_query(fields, "page_limit")
        page_limit: int | None = None
        if raw_limit:
            try:
                page_limit = int(raw_limit)
            except ValueError:
                self._write_error(
                    400,
                    "page_limit must be an integer. "
                    "Supply an account-ledger page_limit, then retry the account-ledger read.",
                )
                return
        try:
            document = lookup_account_ledger(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                chart_account_code,
                fiscal_period_reference=_first_query(fields, "fiscal_period_reference"),
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor"),
            )
        except AccountingValidationError as error:
            message = str(error)
            if "page_limit" in message or "cursor" in message:
                self._write_error(400, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_journal_reversals(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("journal-reversal list")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        if not legal_entity_reference:
            self._write_error(
                400,
                "legal_entity_reference is required. "
                "Supply that journal-reversal list field, then retry the journal-reversal list.",
            )
            return
        raw_limit = _first_query(fields, "page_limit")
        page_limit: int | None = None
        if raw_limit:
            try:
                page_limit = int(raw_limit)
            except ValueError:
                self._write_error(
                    400,
                    "page_limit must be an integer. "
                    "Supply a journal-reversal page_limit, then retry the journal-reversal list.",
                )
                return
        try:
            document = lookup_journal_reversals(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                _first_query(fields, "original_journal_reference"),
                _first_query(fields, "fiscal_period_reference"),
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor"),
            )
        except AccountingValidationError as error:
            message = str(error)
            if "page_limit" in message or "cursor" in message:
                self._write_error(400, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_period_closes(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("period-close list")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        if not legal_entity_reference:
            self._write_error(
                400,
                "legal_entity_reference is required. "
                "Supply that period-close list field, then retry the period-close list.",
            )
            return
        raw_limit = _first_query(fields, "page_limit")
        page_limit: int | None = None
        if raw_limit:
            try:
                page_limit = int(raw_limit)
            except ValueError:
                self._write_error(
                    400,
                    "page_limit must be an integer. "
                    "Supply a period-close page_limit, then retry the period-close list.",
                )
                return
        try:
            document = lookup_period_closes(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                _first_query(fields, "fiscal_period_reference"),
                _first_query(fields, "period_status_code"),
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor"),
            )
        except AccountingValidationError as error:
            message = str(error)
            if (
                "page_limit" in message
                or "cursor" in message
                or "period_status_code" in message
            ):
                self._write_error(400, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_posted_journal(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("journal read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        idempotency_key = _first_query(fields, "idempotency_key")
        journal_reference = _first_query(fields, "journal_reference")
        if idempotency_key or journal_reference:
            try:
                document = lookup_posted_journal(
                    self.server.database_url,
                    tenant_header,
                    idempotency_key=idempotency_key,
                    journal_reference=journal_reference,
                )
            except AccountingValidationError as error:
                self._write_error(404, str(error))
                return
            self._write_json(200, document)
            return
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        if not book_reference:
            book_reference = _first_query(fields, "accounting_book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        if legal_entity_reference or book_reference or fiscal_period_reference:
            if not legal_entity_reference or not book_reference or not fiscal_period_reference:
                self._write_error(
                    400,
                    "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                    "Supply those journal-list fields, then retry the journal list.",
                )
                return
            raw_limit = _first_query(fields, "page_limit")
            page_limit: int | None = None
            if raw_limit:
                try:
                    page_limit = int(raw_limit)
                except ValueError:
                    self._write_error(
                        400,
                        "page_limit must be an integer. "
                        "Supply a journal-list page_limit, then retry the journal list.",
                    )
                    return
            try:
                document = lookup_period_journals(
                    self.server.database_url,
                    tenant_header,
                    legal_entity_reference,
                    book_reference,
                    fiscal_period_reference,
                    page_limit=page_limit,
                    cursor=_first_query(fields, "cursor"),
                )
            except AccountingValidationError as error:
                message = str(error)
                if "page_limit" in message or "cursor" in message:
                    self._write_error(400, message)
                    return
                self._write_error(404, message)
                return
            self._write_json(200, document)
            return
        self._write_error(
            400,
            "idempotency_key, journal_reference, or a period list "
            "(legal_entity_reference, book_reference, fiscal_period_reference) is required. "
            "Supply the Billing key, the posted journal reference, or those list fields, "
            "then retry the journal read.",
        )

    def _get_outbox_events(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("outbox read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        event_type_code = _first_query(fields, "event_type_code")
        if not event_type_code:
            self._write_error(
                400,
                "event_type_code is required. "
                "Supply posting_receipt, period_close, or journal_reversal, then retry the outbox read.",
            )
            return
        raw_limit = _first_query(fields, "page_limit")
        page_limit: int | None = None
        if raw_limit:
            try:
                page_limit = int(raw_limit)
            except ValueError:
                self._write_error(
                    400,
                    "page_limit must be an integer. "
                    "Supply an outbox-event page_limit, then retry the outbox read.",
                )
                return
        try:
            document = lookup_outbox_events(
                self.server.database_url,
                tenant_header,
                event_type_code,
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor"),
            )
        except AccountingValidationError as error:
            self._write_error(400, str(error))
            return
        self._write_json(200, document)

    def _get_audit_events(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("audit-event read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        raw_limit = _first_query(fields, "page_limit")
        page_limit: int | None = None
        if raw_limit:
            try:
                page_limit = int(raw_limit)
            except ValueError:
                self._write_error(
                    400,
                    "page_limit must be an integer. "
                    "Supply an audit-event page_limit, then retry the audit-event read.",
                )
                return
        try:
            document = lookup_audit_events(
                self.server.database_url,
                tenant_header,
                _first_query(fields, "event_type_code"),
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor"),
            )
        except AccountingValidationError as error:
            self._write_error(400, str(error))
            return
        self._write_json(200, document)

    def _post_outbox_publish(self, outbox_event_id: str) -> None:
        tenant_header = self._bound_tenant_header("outbox publish")
        if tenant_header is None:
            return
        try:
            document = publish_outbox_event(
                self.server.database_url, tenant_header, outbox_event_id
            )
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(200, document)

    def _get_fiscal_period(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("period read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        if not legal_entity_reference:
            self._write_error(
                400,
                "legal_entity_reference is required. "
                "Supply that period field, then retry the period read.",
            )
            return
        if fiscal_period_reference:
            try:
                document = lookup_fiscal_period(
                    self.server.database_url,
                    tenant_header,
                    legal_entity_reference,
                    fiscal_period_reference,
                )
            except AccountingValidationError as error:
                self._write_error(404, str(error))
                return
            self._write_json(200, document)
            return
        raw_limit = _first_query(fields, "page_limit")
        page_limit: int | None = None
        if raw_limit:
            try:
                page_limit = int(raw_limit)
            except ValueError:
                self._write_error(
                    400,
                    "page_limit must be an integer. "
                    "Supply a fiscal-period-list page_limit, then retry the period list.",
                )
                return
        try:
            document = lookup_fiscal_periods(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor"),
            )
        except AccountingValidationError as error:
            message = str(error)
            if "page_limit" in message or "cursor" in message:
                self._write_error(400, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _post_fiscal_period(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("period open")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a period-open command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "open tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the period open to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_period_open(
                payload, self.server.database_url, tenant_header
            )
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
        self._write_json(200, document)

    def _post_adjusting_journal(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("journal")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "an AIS adjusting journal")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "adjusting journal tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the journal to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_adjusting_journal(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(409, f"{error}. Supply a new idempotency key, then retry.")
            return
        except AccountingValidationError as error:
            self._write_error(_adjusting_journal_status(error), str(error))
            return
        self._write_json(200, document)

    def _post_journal_proposal(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("proposal")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a Billing accounting_journal_proposal")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "proposal tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the proposal to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_journal_proposal(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(409, f"{error}. Supply a new idempotency key, then retry.")
            return
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
        self._write_json(200, document)

    def _post_journal_reversal(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("reversal")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a journal-reversal command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "reversal tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the reverse to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_journal_reversal(
                payload, self.server.database_url, tenant_header
            )
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
        self._write_json(200, document)

    def _post_period_close(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("close")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a period-close command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "close tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the close to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_period_close(
                payload, self.server.database_url, tenant_header
            )
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
        self._write_json(200, document)

    def _post_billing_proposal_pull(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("pull")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a billing-proposal-pull command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "pull tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the pull to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_billing_proposal_pull(
                payload, self.server.database_url, tenant_header
            )
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
        self._write_json(200, document)

    def _bound_tenant_header(self, mismatch_action: str) -> str | None:
        tenant_header = self.headers.get(TENANT_HEADER)
        if not tenant_header:
            self._write_error(
                400,
                f"{TENANT_HEADER} is required. Supply that tenant header, then retry.",
            )
            return None
        if tenant_header != self.server.tenant_reference:
            self._write_error(
                403,
                f"{TENANT_HEADER} does not match this AIS tenant binding. "
                f"Send the {mismatch_action} to that tenant's endpoint, then retry.",
            )
            return None
        return tenant_header

    def _read_json_object(self, raw_body: bytes, supply_what: str) -> dict[str, object] | None:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_error(
                400,
                f"request body must be JSON. Supply {supply_what}, then retry.",
            )
            return None
        if not isinstance(payload, dict):
            self._write_error(
                400,
                f"request body must be a JSON object. Supply {supply_what}, then retry.",
            )
            return None
        return payload

    def _read_body(self) -> bytes:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError:
            return b""
        if length < 1:
            return b""
        return self.rfile.read(length)

    def _write_error(self, status_code: int, error_message: str) -> None:
        self._write_json(status_code, {"error_message": error_message})

    def _write_json(self, status_code: int, document: dict[str, object]) -> None:
        payload = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _adjusting_journal_status(error: AccountingValidationError) -> int:
    message = str(error)
    if "hard_closed" in message:
        return 409
    if "Chart account " in message:
        return 422
    if "is not recorded" in message:
        return 404
    return 422


def _first_query(fields: dict[str, list[str]], name: str) -> str:
    values = fields.get(name, [])
    return values[0] if values else ""


def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
) -> JournalProposalServer:
    """Create a stdlib HTTP server that posts Billing proposals, AIS adjusting journals, pulls, closes, opens periods, and reads TB, statements, journals, reversals, outbox, and audit history."""
    if not database_url:
        raise AccountingValidationError(
            "ACCOUNTING_DATABASE_URL is empty. Set a PostgreSQL 18 URL and retry posting."
        )
    _require_reference(tenant_reference, "tenant reference")
    return JournalProposalServer((host, port), database_url, tenant_reference)


def run_journal_proposal_server(
    database_url: str | None = None,
    tenant_reference: str | None = None,
    host: str | None = None,
    port: int | None = None,
    serve: Callable[[], None] | None = None,
) -> JournalProposalServer:
    """Bind 0.0.0.0:$PORT by default and serve AIS HTTP commands."""
    resolved_url = (
        database_url
        if database_url is not None
        else os.environ.get("ACCOUNTING_DATABASE_URL", "")
    )
    resolved_tenant = (
        tenant_reference
        if tenant_reference is not None
        else os.environ.get("ACCOUNTING_TENANT_REFERENCE", "")
    )
    resolved_host = "0.0.0.0" if host is None else host
    if port is None:
        port_text = os.environ.get("PORT", "8080")
        try:
            resolved_port = int(port_text)
        except ValueError as error:
            raise AccountingValidationError(
                "PORT must be an integer. Set PORT to the listen port, then retry."
            ) from error
    else:
        resolved_port = port
    server = create_journal_proposal_server(
        resolved_url, resolved_tenant, resolved_host, resolved_port
    )
    runner = server.serve_forever if serve is None else serve
    runner()
    return server
