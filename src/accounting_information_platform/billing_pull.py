"""Pull Billing journal-proposal GET pages and post validated items in AIS."""

from __future__ import annotations

import http.client
import json
import os
import ssl
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import ParseResult, urlencode, urlparse

from .accept import accept_journal_proposal
from .core import AccountingValidationError, _require_reference


TENANT_HEADER = "X-CWL-Tenant-Reference"
VALIDATED_PROPOSAL_STATUS = "validated"
LIST_COLLECTION_KEY = "journal_proposals"
LIST_CURSOR_KEY = "next_cursor"
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
MAX_PULL_PAGES = 20
_REJECTION_REASON_RULES = (
    (("is not mapped", "account_role_mapping"), "unknown_account_role"),
    (("must balance",), "unbalanced_journal"),
    (
        ("closed fiscal period", "open period", "is soft_closed", "is hard_closed"),
        "closed_period",
    ),
    (("bound tenant",), "cross_tenant"),
    (("canonical decimal", "amount must be"), "invalid_amount"),
)


@dataclass(frozen=True, slots=True)
class JournalProposalPage:
    """One Billing #15 list page after AIS drops non-validated wire items."""

    journal_proposals: tuple[dict[str, object], ...]
    next_cursor: str | None


def pull_validated_journal_proposals(
    billing_base_url: str,
    tenant_reference: str,
    *,
    proposed_after: str | None = None,
    cursor: str | None = None,
    page_limit: int | None = None,
) -> JournalProposalPage:
    """GET one Billing journal-proposal page and keep `validated` proposals only."""
    query: dict[str, str] = {
        "tenant_reference": tenant_reference,
        "proposal_status": VALIDATED_PROPOSAL_STATUS,
        "page_limit": str(_resolve_page_limit(page_limit)),
    }
    if proposed_after:
        query["proposed_after"] = proposed_after
    if cursor:
        query["cursor"] = cursor
    document = _billing_get(
        f"{_require_billing_base_url(billing_base_url)}/v1/journal-proposals",
        tenant_reference,
        query,
    )
    if "items" in document or "cursor" in document:
        raise AccountingValidationError(
            "Billing list envelope used items or cursor. "
            "Ask Billing to correct the published list contract "
            "(journal_proposals + next_cursor), then retry the pull."
        )
    raw_items = document.get(LIST_COLLECTION_KEY)
    if not isinstance(raw_items, list):
        raise AccountingValidationError(
            "Billing list envelope is missing journal_proposals. "
            "Ask Billing to correct the published list contract, then retry the pull."
        )
    validated = tuple(
        item
        for item in raw_items
        if isinstance(item, dict) and item.get("proposal_status") == VALIDATED_PROPOSAL_STATUS
    )
    raw_cursor = document.get(LIST_CURSOR_KEY)
    next_cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None
    return JournalProposalPage(validated, next_cursor)


def pull_journal_proposal(
    billing_base_url: str,
    tenant_reference: str,
    proposal_id: str,
) -> dict[str, object]:
    """GET one same-tenant Billing proposal; treat 404 as unknown or cross-tenant."""
    if not proposal_id:
        raise AccountingValidationError(
            "proposal_id is required. Supply the Billing proposal_id, then retry the pull."
        )
    document = _billing_get(
        (
            f"{_require_billing_base_url(billing_base_url)}"
            f"/v1/journal-proposals/{proposal_id}"
        ),
        tenant_reference,
        {"tenant_reference": tenant_reference},
    )
    if document.get("proposal_status") != VALIDATED_PROPOSAL_STATUS:
        raise AccountingValidationError(
            "Billing journal proposal is not validated. "
            "Ask Billing for a validated proposal, then retry the pull."
        )
    return document


def accept_pulled_proposals(
    billing_base_url: str,
    database_url: str,
    tenant_reference: str,
    *,
    proposed_after: str | None = None,
    cursor: str | None = None,
    page_limit: int | None = None,
) -> dict[str, object]:
    """Pull Billing pages until `next_cursor` is empty; keep receipts and rejects."""
    receipts: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    page_cursor = cursor
    seen_cursors: set[str] = set()
    pages_fetched = 0
    while True:
        pages_fetched += 1
        if pages_fetched > MAX_PULL_PAGES:
            raise AccountingValidationError(
                "Billing pull exceeded 20 pages. "
                "Narrow proposed_after or cursor, then retry the pull."
            )
        page = pull_validated_journal_proposals(
            billing_base_url,
            tenant_reference,
            proposed_after=proposed_after,
            cursor=page_cursor,
            page_limit=page_limit,
        )
        for item in page.journal_proposals:
            try:
                receipts.append(
                    accept_journal_proposal(item, database_url, tenant_reference)
                )
            except AccountingValidationError as error:
                rejected.append(_rejected_proposal(item, error))
        if page_cursor:
            seen_cursors.add(page_cursor)
        if not page.next_cursor:
            break
        if page.next_cursor in seen_cursors:
            raise AccountingValidationError(
                "Billing list cursor did not advance. "
                "Ask Billing to fix the list cursor, then retry the pull."
            )
        page_cursor = page.next_cursor
    return {"posting_receipts": receipts, "rejected_proposals": rejected}


def accept_billing_proposal_pull(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Run a Billing pull command for *tenant_reference* and return receipts plus rejects."""
    if not isinstance(payload, Mapping):
        raise AccountingValidationError(
            "billing proposal pull payload must be a JSON object. "
            "Supply a billing-proposal-pull command, then retry the pull."
        )
    if payload.get("tenant_reference") != tenant_reference:
        raise AccountingValidationError(
            "pull tenant_reference does not match the bound tenant. "
            "Call accept_billing_proposal_pull with that tenant_reference, then retry."
        )
    billing_base_url = str(
        payload.get("billing_base_url") or os.environ.get("BILLING_BASE_URL", "")
    )
    raw_page_limit = payload.get("page_limit")
    if raw_page_limit in (None, ""):
        page_limit = None
    else:
        try:
            page_limit = int(raw_page_limit)
        except (TypeError, ValueError) as error:
            raise AccountingValidationError(
                "page_limit must be an integer. "
                "Supply a Billing page_limit, then retry the pull."
            ) from error
    proposed_after = payload.get("proposed_after")
    cursor = payload.get("cursor")
    return accept_pulled_proposals(
        billing_base_url,
        database_url,
        tenant_reference,
        proposed_after=str(proposed_after) if proposed_after else None,
        cursor=str(cursor) if cursor else None,
        page_limit=page_limit,
    )


def _rejection_reason_code(error: BaseException) -> str:
    message = str(error)
    for needles, reason_code in _REJECTION_REASON_RULES:
        if any(needle in message for needle in needles):
            return reason_code
    return "proposal_validation_failed"


def _rejected_proposal(
    item: Mapping[str, object], error: AccountingValidationError
) -> dict[str, object]:
    return {
        "proposal_id": str(item.get("proposal_id") or ""),
        "idempotency_key": str(item.get("idempotency_key") or ""),
        "rejection_reason_code": _rejection_reason_code(error),
        "rejection_message": str(error),
    }


def _resolve_page_limit(page_limit: int | None) -> int:
    if page_limit is None:
        return DEFAULT_PAGE_LIMIT
    if page_limit < 1 or page_limit > MAX_PAGE_LIMIT:
        raise AccountingValidationError(
            "page_limit must be between 1 and 100. "
            "Supply a Billing page_limit, then retry the pull."
        )
    return page_limit


def _require_billing_base_url(billing_base_url: str) -> str:
    stripped = billing_base_url.strip().rstrip("/")
    if not stripped:
        raise AccountingValidationError(
            "BILLING_BASE_URL is empty. "
            "Set BILLING_BASE_URL to the Billing origin, then retry the pull."
        )
    return stripped


def _billing_get(
    url: str,
    tenant_reference: str,
    query: Mapping[str, str],
) -> dict[str, object]:
    _require_reference(tenant_reference, "tenant reference")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AccountingValidationError(
            "BILLING_BASE_URL must be an http or https origin. "
            "Set BILLING_BASE_URL to the Billing origin, then retry the pull."
        )
    request_path = f"{parsed.path}?{urlencode(query)}"
    try:
        connection = _open_billing_connection(parsed)
        try:
            connection.request(
                "GET",
                request_path,
                headers={TENANT_HEADER: tenant_reference, "Accept": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read()
            status = response.status
        finally:
            connection.close()
    except OSError as error:
        raise AccountingValidationError(
            "Billing journal-proposal pull could not be reached. "
            "Retry the Billing pull after Billing recovers."
        ) from error
    if status >= 400:
        raise _billing_http_error(status)
    return _parse_billing_object(raw)


def _open_billing_connection(parsed: ParseResult) -> http.client.HTTPConnection:
    port = parsed.port
    if parsed.scheme == "https" and port is None:
        port = 443
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=5)
    if parsed.scheme == "https":
        connection.connect()
        connection.sock = ssl.create_default_context().wrap_socket(
            connection.sock, server_hostname=parsed.hostname
        )
    return connection


def _billing_http_error(status: int) -> AccountingValidationError:
    if status == 404:
        return AccountingValidationError(
            "Billing journal proposal was not found for this tenant. "
            "Do not retry as another tenant. Confirm the proposal_id, then retry the pull."
        )
    if status == 422:
        return AccountingValidationError(
            "Billing rejected the journal-proposal pull. "
            "Ask Billing to correct the tenant header and tenant_reference query, "
            "then retry the pull."
        )
    if status >= 500:
        return AccountingValidationError(
            "Billing journal-proposal pull failed. "
            "Retry the Billing pull after Billing recovers."
        )
    return AccountingValidationError(
        f"Billing journal-proposal pull returned HTTP {status}. "
        "Ask Billing to correct the pull contract, then retry the pull."
    )


def _parse_billing_object(raw: bytes) -> dict[str, object]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AccountingValidationError(
            "Billing journal-proposal pull returned non-JSON. "
            "Ask Billing to correct the published contract, then retry the pull."
        ) from error
    if not isinstance(document, dict):
        raise AccountingValidationError(
            "Billing journal-proposal pull must return a JSON object. "
            "Ask Billing to correct the published contract, then retry the pull."
        )
    return document
