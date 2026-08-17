"""Thin stdlib HTTP boundary for Billing journal proposals and receipt lookup."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .accept import accept_journal_proposal, lookup_published_receipt
from .core import AccountingValidationError, IdempotencyConflictError, _require_reference


TENANT_HEADER = "X-CWL-Tenant-Reference"
JOURNAL_PROPOSAL_PATH = "/journal-proposals"
POSTING_RECEIPT_PATH = "/posting-receipts"


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
    """Accept POST /journal-proposals and GET /posting-receipts for one tenant."""

    server: JournalProposalServer

    def do_GET(self) -> None:
        """Return a persisted posting receipt by Billing idempotency key."""
        parsed = urlparse(self.path)
        if parsed.path == JOURNAL_PROPOSAL_PATH:
            self._write_error(
                405,
                "GET is not supported on the journal proposal endpoint. "
                "POST a Billing accounting_journal_proposal, then retry.",
            )
            return
        if parsed.path != POSTING_RECEIPT_PATH:
            self._write_error(
                404,
                "unknown path. GET /posting-receipts?idempotency_key=, then retry.",
            )
            return
        tenant_header = self.headers.get(TENANT_HEADER)
        if not tenant_header:
            self._write_error(
                400,
                f"{TENANT_HEADER} is required. Supply that tenant header, then retry.",
            )
            return
        if tenant_header != self.server.tenant_reference:
            self._write_error(
                403,
                f"{TENANT_HEADER} does not match this AIS tenant binding. "
                "Send the lookup to that tenant's endpoint, then retry.",
            )
            return
        keys = parse_qs(parsed.query).get("idempotency_key", [])
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

    def do_POST(self) -> None:
        """Ingest one Billing proposal for the purpose-limited tenant header."""
        raw_body = self._read_body()
        if self.path != JOURNAL_PROPOSAL_PATH:
            self._write_error(
                404,
                "unknown path. POST /journal-proposals, then retry.",
            )
            return
        tenant_header = self.headers.get(TENANT_HEADER)
        if not tenant_header:
            self._write_error(
                400,
                f"{TENANT_HEADER} is required. Supply that tenant header, then retry.",
            )
            return
        if tenant_header != self.server.tenant_reference:
            self._write_error(
                403,
                f"{TENANT_HEADER} does not match this AIS tenant binding. "
                "Send the proposal to that tenant's endpoint, then retry.",
            )
            return
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_error(
                400,
                "request body must be JSON. Supply a Billing accounting_journal_proposal, "
                "then retry.",
            )
            return
        if not isinstance(payload, dict):
            self._write_error(
                400,
                "request body must be a JSON object. "
                "Supply a Billing accounting_journal_proposal, then retry.",
            )
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

    def log_message(self, format: str, *args: object) -> None:
        """Omit request logs so receipts and tenant URNs are not written to stdout."""
        return

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


def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
) -> JournalProposalServer:
    """Create a stdlib HTTP server that posts proposals and looks up receipts."""
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
    """Bind 0.0.0.0:$PORT by default and serve proposal POST and receipt GET."""
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
