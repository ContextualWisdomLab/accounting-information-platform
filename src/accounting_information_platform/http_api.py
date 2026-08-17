"""Thin stdlib HTTP boundary for Billing proposals, pulls, receipts, close, TB, catalog, and journals."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .accept import (
    accept_journal_proposal,
    accept_journal_reversal,
    accept_period_close,
    lookup_account_role_mappings,
    lookup_posted_journal,
    lookup_published_receipt,
    lookup_trial_balance,
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
ACCOUNT_ROLE_MAPPING_PATH = "/account-role-mappings"
JOURNAL_PATH = "/journals"


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
    """Serve proposal POST, reverse, pull, receipt GET, close, TB, catalog, journal inquiry, and healthz."""

    server: JournalProposalServer

    def do_GET(self) -> None:
        """Route healthz, receipt, trial-balance, catalog, and journal-inquiry reads, and GET 405s."""
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
            self._write_error(
                405,
                "GET is not supported on the journal reversal endpoint. "
                "POST a journal-reversal command, then retry.",
            )
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
        if parsed.path == ACCOUNT_ROLE_MAPPING_PATH:
            self._get_account_role_mappings(parsed.query)
            return
        if parsed.path == JOURNAL_PATH:
            self._get_posted_journal(parsed.query)
            return
        self._write_error(
            404,
            "unknown path. GET /posting-receipts?idempotency_key=, GET /trial-balances, "
            "GET /account-role-mappings, or GET /journals, then retry.",
        )

    def do_POST(self) -> None:
        """Route journal-proposal accept, reverse, Billing pull, close, and GET-only POST 405s."""
        raw_body = self._read_body()
        parsed_path = urlparse(self.path).path
        if parsed_path == JOURNAL_PATH:
            self._write_error(
                405,
                "POST is not supported on the journal inquiry endpoint. "
                "GET the posted journal, then retry.",
            )
            return
        if parsed_path == ACCOUNT_ROLE_MAPPING_PATH:
            self._write_error(
                405,
                "POST is not supported on the account role mapping endpoint. "
                "GET the catalog mappings, then retry.",
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
        self._write_error(
            404,
            "unknown path. POST /journal-proposals, POST /journal-reversals, "
            "POST /billing-proposal-pulls, or POST /period-closes, then retry.",
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

    def _get_posted_journal(self, query: str) -> None:
        tenant_header = self._bound_tenant_header("journal read")
        if tenant_header is None:
            return
        fields = parse_qs(query)
        idempotency_key = _first_query(fields, "idempotency_key")
        journal_reference = _first_query(fields, "journal_reference")
        if not idempotency_key and not journal_reference:
            self._write_error(
                400,
                "idempotency_key or journal_reference is required. "
                "Supply the Billing key or the posted journal reference, then retry the journal read.",
            )
            return
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


def _first_query(fields: dict[str, list[str]], name: str) -> str:
    values = fields.get(name, [])
    return values[0] if values else ""


def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
) -> JournalProposalServer:
    """Create a stdlib HTTP server that posts, pulls, closes, and reads TB, catalog, and journals."""
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
