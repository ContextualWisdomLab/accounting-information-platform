"""Thin stdlib HTTP boundary for accounting commands, reads, and operations."""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .accept import (
    HomeTaxRequestValidationError,
    accept_adjusting_journal,
    accept_bank_account_assignment,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    accept_home_tax_submission,
    accept_journal_proposal,
    accept_journal_reversal,
    accept_period_close,
    accept_period_open,
    lookup_account_balances,
    lookup_bank_statement,
    lookup_bank_statement_entries,
    lookup_bank_statements,
    lookup_account_rollforward,
    lookup_account_ledger,
    lookup_account_role_mappings,
    lookup_accounting_books,
    lookup_chart_accounts,
    lookup_audit_events,
    lookup_legal_entities,
    lookup_financial_statement,
    lookup_financial_statement_package,
    lookup_fiscal_period,
    lookup_fiscal_periods,
    lookup_journal_reversals,
    lookup_payable_aging,
    lookup_period_close_package,
    lookup_period_closes,
    lookup_outbox_events,
    lookup_period_journals,
    lookup_posted_journal,
    lookup_published_receipt,
    lookup_receivable_aging,
    lookup_trial_balance,
    lookup_home_tax_submissions,
    lookup_unapplied_cash_rollforward,
    lookup_vat_period_register,
    publish_outbox_event,
)
from .bank_statement import MemoryArtifactStore
from .billing_pull import accept_billing_proposal_pull
from .core import AccountingValidationError, IdempotencyConflictError, _require_reference
from .persistence import PostgresPostingLedger


TENANT_HEADER = "X-CWL-Tenant-Reference"
HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"
BILLING_PROPOSAL_PULL_PATH = "/billing-proposal-pulls"
JOURNAL_PROPOSAL_PATH = "/journal-proposals"
JOURNAL_REVERSAL_PATH = "/journal-reversals"
PERIOD_CLOSE_PATH = "/period-closes"
POSTING_RECEIPT_PATH = "/posting-receipts"
TRIAL_BALANCE_PATH = "/trial-balances"
FINANCIAL_STATEMENT_PATH = "/financial-statements"
FINANCIAL_STATEMENT_PACKAGE_PATH = "/financial-statement-packages"
ACCOUNT_ROLE_MAPPING_PATH = "/account-role-mappings"
ACCOUNTING_BOOK_PATH = "/accounting-books"
LEGAL_ENTITY_PATH = "/legal-entities"
CHART_ACCOUNT_PATH = "/chart-accounts"
ACCOUNT_LEDGER_PATH = "/account-ledgers"
ACCOUNT_BALANCE_PATH = "/account-balances"
ACCOUNT_ROLLFORWARD_PATH = "/account-rollforwards"
UNAPPLIED_CASH_ROLLFORWARD_PATH = "/unapplied-cash-rollforwards"
VAT_PERIOD_REGISTER_PATH = "/vat-period-registers"
HOME_TAX_SUBMISSION_PATH = "/home-tax-submissions"
BANK_ACCOUNT_PATH = "/bank-accounts"
BANK_ACCOUNT_ASSIGNMENT_PATH = "/bank-account-assignments"
BANK_STATEMENT_PATH = "/bank-statements"
BANK_STATEMENT_ENTRY_PATH = "/bank-statement-entries"
RECEIVABLE_AGING_PATH = "/receivable-agings"
PAYABLE_AGING_PATH = "/payable-agings"
PERIOD_CLOSE_PACKAGE_PATH = "/period-close-packages"
JOURNAL_PATH = "/journals"
FISCAL_PERIOD_PATH = "/fiscal-periods"
OUTBOX_PATH = "/outbox-events"
AUDIT_EVENT_PATH = "/audit-events"
_OUTBOX_PUBLISH_PATH = re.compile(
    r"^/outbox-events/"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"/publish$"
)
_MAX_REQUEST_BODY_BYTES = 1_048_576


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
        self.artifact_store = MemoryArtifactStore()
        super().__init__(server_address, JournalProposalHandler)


class JournalProposalHandler(BaseHTTPRequestHandler):
    """Serve accounting commands and reads plus liveness and readiness probes."""

    server: JournalProposalServer

    def do_GET(self) -> None:
        """Route operational probes, accounting reads, and GET-only 405s."""
        parsed = urlparse(self.path)
        if parsed.path == HEALTHZ_PATH:
            self._write_json(200, {"status": "ok"})
            return
        if parsed.path == READYZ_PATH:
            self._get_readyz()
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
        if parsed.path == FINANCIAL_STATEMENT_PACKAGE_PATH:
            self._get_financial_statement_package(parsed.query)
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
        if parsed.path == UNAPPLIED_CASH_ROLLFORWARD_PATH:
            self._get_unapplied_cash_rollforward(parsed.query)
            return
        if parsed.path == VAT_PERIOD_REGISTER_PATH:
            self._get_vat_period_register(parsed.query)
            return
        if parsed.path == HOME_TAX_SUBMISSION_PATH:
            self._get_home_tax_submissions(parsed.query)
            return
        if parsed.path == BANK_ACCOUNT_PATH:
            self._write_error(
                405,
                "GET is not supported on the bank-account register endpoint. "
                "POST a bank-account command, then retry.",
            )
            return
        if parsed.path == BANK_ACCOUNT_ASSIGNMENT_PATH:
            self._write_error(
                405,
                "GET is not supported on the bank-account-assignment endpoint. "
                "POST a bank-account assignment, then retry.",
            )
            return
        if parsed.path == BANK_STATEMENT_PATH:
            self._get_bank_statements(parsed.query)
            return
        if parsed.path == BANK_STATEMENT_ENTRY_PATH:
            self._get_bank_statement_entries(parsed.query)
            return
        if parsed.path == RECEIVABLE_AGING_PATH:
            self._get_receivable_aging(parsed.query)
            return
        if parsed.path == PAYABLE_AGING_PATH:
            self._get_payable_aging(parsed.query)
            return
        if parsed.path == PERIOD_CLOSE_PACKAGE_PATH:
            self._get_period_close_package(parsed.query)
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
            "unknown path. GET /healthz, GET /readyz, GET /posting-receipts?idempotency_key=, "
            "GET /trial-balances, "
            "GET /financial-statements, GET /financial-statement-packages, "
            "GET /account-role-mappings, GET /accounting-books, "
            "GET /legal-entities, GET /chart-accounts, GET /account-ledgers, "
            "GET /account-balances, GET /account-rollforwards, GET /unapplied-cash-rollforwards, "
            "GET /vat-period-registers, GET /home-tax-submissions, GET /bank-statements, "
            "GET /bank-statement-entries, GET /receivable-agings, "
            "GET /payable-agings, GET /period-close-packages, GET /journals, "
            "GET /journal-reversals, GET /period-closes, GET /fiscal-periods, "
            "GET /outbox-events?event_type_code=, or GET /audit-events, then retry.",
        )

    def _get_readyz(self) -> None:
        """Return readiness only when the bound tenant and core schema are usable."""
        try:
            PostgresPostingLedger(
                self.server.database_url, self.server.tenant_reference
            ).check_readiness()
        except AccountingValidationError:
            self._write_json(
                503,
                {
                    "status": "not_ready",
                    "error_message": (
                        "Accounting service is not ready. Verify PostgreSQL 18 "
                        "connectivity, migrations, and tenant provisioning, then retry."
                    ),
                },
            )
            return
        self._write_json(200, {"status": "ready"})

    def do_POST(self) -> None:
        """Route journal-proposal accept, adjusting journal, reverse, Billing pull, close, outbox publish, audit-history 405, and GET-only POST 405s."""
        raw_body = self._read_body()
        if raw_body is None:
            return
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
        if parsed_path == FINANCIAL_STATEMENT_PACKAGE_PATH:
            self._write_error(
                405,
                "POST is not supported on the financial statement package endpoint. "
                "GET the financial-statement package, then retry.",
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
        if parsed_path == UNAPPLIED_CASH_ROLLFORWARD_PATH:
            self._write_error(
                405,
                "POST is not supported on the unapplied-cash rollforward endpoint. "
                "GET the unapplied-cash rollforward, then retry.",
            )
            return
        if parsed_path == VAT_PERIOD_REGISTER_PATH:
            self._write_error(
                405,
                "POST is not supported on the vat-period-register endpoint. "
                "GET the vat-period-register, then retry.",
            )
            return
        if parsed_path == RECEIVABLE_AGING_PATH:
            self._write_error(
                405,
                "POST is not supported on the receivable aging endpoint. "
                "GET the receivable aging, then retry.",
            )
            return
        if parsed_path == PAYABLE_AGING_PATH:
            self._write_error(
                405,
                "POST is not supported on the payable aging endpoint. "
                "GET the payable aging, then retry.",
            )
            return
        if parsed_path == PERIOD_CLOSE_PACKAGE_PATH:
            self._write_error(
                405,
                "POST is not supported on the period-close package endpoint. "
                "GET the period-close package, then retry.",
            )
            return
        if parsed_path == JOURNAL_PROPOSAL_PATH:
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
        if parsed_path == HOME_TAX_SUBMISSION_PATH:
            self._post_home_tax_submission(raw_body)
            return
        if parsed_path == BANK_ACCOUNT_PATH:
            self._post_bank_account(raw_body)
            return
        if parsed_path == BANK_ACCOUNT_ASSIGNMENT_PATH:
            self._post_bank_account_assignment(raw_body)
            return
        if parsed_path == BANK_STATEMENT_PATH:
            self._post_bank_statement(raw_body)
            return
        if parsed_path == BANK_STATEMENT_ENTRY_PATH:
            self._write_error(
                405,
                "POST is not supported on the bank-statement-entry list endpoint. "
                "GET the statement entries, then retry.",
            )
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
            "POST /billing-proposal-pulls, POST /period-closes, POST /home-tax-submissions, "
            "POST /bank-accounts, POST /bank-account-assignments, POST /bank-statements, "
            "POST /fiscal-periods, "
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
        balance_basis_code = _first_query(fields, "balance_basis_code")
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
                balance_basis_code,
            )
        except AccountingValidationError as error:
            message = str(error)
            if "must be unadjusted, adjusted, or post_close" in message:
                self._write_error(400, message)
                return
            if "post_close requires stored close evidence" in message:
                self._write_error(409, message)
                return
            self._write_error(404, message)
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

    def _get_financial_statement_package(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("financial-statement-package read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        comparison_fiscal_period_reference = _first_query(
            fields, "comparison_fiscal_period_reference"
        )
        statement_scope_code = _first_query(fields, "statement_scope_code")
        if not legal_entity_reference or not book_reference or not fiscal_period_reference:
            self._write_error(
                400,
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those financial-statement-package fields, then retry the financial-statement-package read.",
            )
            return
        try:
            document = lookup_financial_statement_package(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
                comparison_fiscal_period_reference,
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

    def _get_unapplied_cash_rollforward(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("unapplied-cash-rollforward read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        if not book_reference:
            book_reference = _first_query(fields, "accounting_book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        if not legal_entity_reference or not book_reference or not fiscal_period_reference:
            self._write_error(
                400,
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those unapplied-cash-rollforward fields, then retry the unapplied-cash-rollforward read.",
            )
            return
        try:
            document = lookup_unapplied_cash_rollforward(
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

    def _get_vat_period_register(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("vat-period-register read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        if not book_reference:
            book_reference = _first_query(fields, "accounting_book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        if not legal_entity_reference or not book_reference or not fiscal_period_reference:
            self._write_error(
                400,
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those vat-period-register fields, then retry the vat-period-register read.",
            )
            return
        try:
            document = lookup_vat_period_register(
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

    def _get_home_tax_submissions(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("home-tax-submission read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        if not book_reference:
            book_reference = _first_query(fields, "accounting_book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        if not legal_entity_reference or not book_reference or not fiscal_period_reference:
            self._write_error(
                400,
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those home-tax-submission fields, then retry the home-tax-submission read.",
            )
            return
        try:
            document = lookup_home_tax_submissions(
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

    def _get_receivable_aging(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("receivable-aging read")
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
                "Supply those receivable-aging fields, then retry the receivable-aging read.",
            )
            return
        try:
            document = lookup_receivable_aging(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
                chart_account_code=_first_query(fields, "chart_account_code"),
            )
        except AccountingValidationError as error:
            message = str(error)
            if "must be the catalog accounts_receivable" in message:
                self._write_error(422, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_payable_aging(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("payable-aging read")
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
                "Supply those payable-aging fields, then retry the payable-aging read.",
            )
            return
        try:
            document = lookup_payable_aging(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
                chart_account_code=_first_query(fields, "chart_account_code"),
            )
        except AccountingValidationError as error:
            message = str(error)
            if "must be the catalog tax_payable" in message:
                self._write_error(422, message)
                return
            self._write_error(404, message)
            return
        self._write_json(200, document)

    def _get_period_close_package(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("period-close-package read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        legal_entity_reference = _first_query(fields, "legal_entity_reference")
        book_reference = _first_query(fields, "book_reference")
        fiscal_period_reference = _first_query(fields, "fiscal_period_reference")
        comparison_fiscal_period_reference = _first_query(
            fields, "comparison_fiscal_period_reference"
        )
        statement_scope_code = _first_query(fields, "statement_scope_code")
        if not legal_entity_reference or not book_reference or not fiscal_period_reference:
            self._write_error(
                400,
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those period-close-package fields, then retry the period-close-package read.",
            )
            return
        try:
            document = lookup_period_close_package(
                self.server.database_url,
                tenant_header,
                legal_entity_reference,
                book_reference,
                fiscal_period_reference,
                comparison_fiscal_period_reference,
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
                self._write_error(_query_validation_status(error), message)
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
                self._write_error(_query_validation_status(error), message)
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
                self._write_error(_query_validation_status(error), message)
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
                    journal_source_code=_first_query(fields, "journal_source_code"),
                )
            except AccountingValidationError as error:
                message = str(error)
                if (
                    "page_limit" in message
                    or "cursor" in message
                    or "journal_source_code" in message
                ):
                    self._write_error(_query_validation_status(error), message)
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
            self._write_error(_query_validation_status(error), str(error))
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
            self._write_error(_query_validation_status(error), str(error))
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
                self._write_error(_query_validation_status(error), message)
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
        except IdempotencyConflictError as error:
            self._write_error(
                409,
                f"{error}. Supply a new reversal command identity, then retry.",
            )
            return
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

    def _post_home_tax_submission(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("home-tax-submission")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a home-tax-submission command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "home-tax-submission tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the home-tax-submission to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_home_tax_submission(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(
                409,
                f"{error}. Supply a new HomeTax idempotency key, then retry.",
            )
            return
        except HomeTaxRequestValidationError as error:
            self._write_error(422, str(error))
            return
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(422, document)

    def _post_bank_account(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("bank-account")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a bank-account command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "bank-account tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the bank-account command to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_bank_account_record(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(409, f"{error}. Supply a new bank_account_reference, then retry.")
            return
        except AccountingValidationError as error:
            self._write_error(422, str(error))
            return
        self._write_json(200, document)

    def _post_bank_account_assignment(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("bank-account-assignment")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a bank-account-assignment command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "bank-account-assignment tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the assignment to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_bank_account_assignment(
                payload, self.server.database_url, tenant_header
            )
        except IdempotencyConflictError as error:
            self._write_error(409, f"{error}. Supply a new assignment key, then retry.")
            return
        except AccountingValidationError as error:
            self._write_error(_bank_statement_status(error), str(error))
            return
        self._write_json(200, document)

    def _post_bank_statement(self, raw_body: bytes) -> None:
        tenant_header = self._bound_tenant_header("bank-statement")
        if tenant_header is None:
            return
        payload = self._read_json_object(raw_body, "a bank-statement command")
        if payload is None:
            return
        if payload.get("tenant_reference") != tenant_header:
            self._write_error(
                403,
                "bank-statement tenant_reference does not match X-CWL-Tenant-Reference. "
                "Send the statement to that tenant's AIS endpoint, then retry.",
            )
            return
        try:
            document = accept_bank_statement_evidence(
                payload,
                self.server.database_url,
                tenant_header,
                artifact_store=self.server.artifact_store,
            )
        except IdempotencyConflictError as error:
            self._write_error(
                409,
                f"{error}. Supply a new ingestion_idempotency_key, then retry.",
            )
            return
        except AccountingValidationError as error:
            self._write_error(_bank_statement_status(error), str(error))
            return
        self._write_json(200, document)

    def _get_bank_statements(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("bank-statement")
        if tenant_header is None:
            return
        fields = parse_qs(query, keep_blank_values=True)
        record_id = _first_query(fields, "bank_statement_record_id")
        if record_id:
            try:
                document = lookup_bank_statement(
                    self.server.database_url, tenant_header, record_id
                )
            except AccountingValidationError as error:
                self._write_error(_bank_statement_status(error), str(error))
                return
            self._write_json(200, document)
            return
        bank_account_reference = _first_query(fields, "bank_account_reference")
        if not bank_account_reference:
            self._write_error(
                400,
                "bank_account_reference or bank_statement_record_id is required. "
                "Supply one of those query keys, then retry the statement read.",
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
                    "Supply a bank-statement-list page_limit, then retry the statement list.",
                )
                return
        try:
            document = lookup_bank_statements(
                self.server.database_url,
                tenant_header,
                bank_account_reference,
                period_start=_first_query(fields, "period_start") or None,
                period_end=_first_query(fields, "period_end") or None,
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor") or None,
            )
        except AccountingValidationError as error:
            self._write_error(_bank_statement_status(error), str(error))
            return
        self._write_json(200, document)

    def _get_bank_statement_entries(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("bank-statement-entry")
        if tenant_header is None:
            return
        fields = parse_qs(query, keep_blank_values=True)
        record_id = _first_query(fields, "bank_statement_record_id")
        if not record_id:
            self._write_error(
                400,
                "bank_statement_record_id is required. "
                "Supply that query key, then retry the entry list.",
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
                    "Supply a bank-statement-entry page_limit, then retry the entry list.",
                )
                return
        try:
            document = lookup_bank_statement_entries(
                self.server.database_url,
                tenant_header,
                record_id,
                page_limit=page_limit,
                cursor=_first_query(fields, "cursor") or None,
            )
        except AccountingValidationError as error:
            self._write_error(_bank_statement_status(error), str(error))
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

    def _read_body(self) -> bytes | None:
        transfer_encoding_values = self.headers.get_all("Transfer-Encoding")
        if transfer_encoding_values:
            self.close_connection = True
            self._write_error(
                400,
                "Transfer-Encoding is not supported. "
                "Send one ASCII decimal Content-Length and an unencoded body, then retry.",
            )
            return None
        length_values = self.headers.get_all("Content-Length")
        if length_values is None or len(length_values) != 1:
            self._write_error(
                400,
                "exactly one Content-Length header is required. "
                "Send one ASCII decimal Content-Length, then retry.",
            )
            return None
        length_text = length_values[0]
        if re.fullmatch(r"[0-9]+", length_text) is None:
            self._write_error(
                400,
                "Content-Length must contain ASCII decimal digits only. "
                "Send one ASCII decimal Content-Length, then retry.",
            )
            return None
        length = int(length_text)
        if length > _MAX_REQUEST_BODY_BYTES:
            self._write_error(
                413,
                "request body exceeds 1 MiB. "
                "Send a smaller JSON command, then retry.",
            )
            return None
        if length < 1:
            return b""
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            self._write_error(
                400,
                "request body is incomplete for Content-Length. "
                "Send the declared number of body bytes, then retry.",
            )
            return None
        return body

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


def _bank_statement_status(error: AccountingValidationError) -> int:
    message = str(error)
    if "is not recorded" in message:
        return 404
    if "page_limit" in message or "cursor" in message or "must be a UUID" in message:
        return 400
    if "UTC" in message and "timestamp" in message:
        return 422
    return 422


def _query_validation_status(error: AccountingValidationError) -> int:
    if "UTC offset" in str(error):
        return 422
    return 400


def _first_query(fields: dict[str, list[str]], name: str) -> str:
    values = fields.get(name, [])
    return values[0] if values else ""


def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
) -> JournalProposalServer:
    """Create a stdlib HTTP server that posts Billing proposals, AIS adjusting journals, pulls, closes, opens periods, accepts bank-statement evidence, and reads TB, statements, journals, reversals, receivable aging, payable aging, outbox, and audit history."""
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
    """Bind 127.0.0.1:$PORT by default and serve AIS HTTP commands."""
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
    resolved_host = "127.0.0.1" if host is None else host
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
