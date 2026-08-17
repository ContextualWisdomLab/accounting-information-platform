"""Pull Billing journal-proposal GET pages and post validated items in AIS."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .accept import accept_journal_proposal
from .core import AccountingValidationError, _require_reference


TENANT_HEADER = "X-CWL-Tenant-Reference"
VALIDATED_PROPOSAL_STATUS = "validated"
LIST_COLLECTION_KEY = "journal_proposals"
LIST_CURSOR_KEY = "next_cursor"


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
    """GET one Billing journal-proposal page and keep `validated` items only."""
    query: dict[str, str] = {
        "tenant_reference": tenant_reference,
        "proposal_status": VALIDATED_PROPOSAL_STATUS,
    }
    if proposed_after:
        query["proposed_after"] = proposed_after
    if cursor:
        query["cursor"] = cursor
    if page_limit is not None:
        query["page_limit"] = str(page_limit)
    document = _billing_get(
        f"{_require_billing_base_url(billing_base_url)}/v1/journal-proposals",
        tenant_reference,
        query,
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
) -> tuple[dict[str, object], ...]:
    """Pull Billing pages until `next_cursor` is empty and post each validated item."""
    receipts: list[dict[str, object]] = []
    page_cursor = cursor
    while True:
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
            except AccountingValidationError:
                continue
        if not page.next_cursor:
            break
        page_cursor = page.next_cursor
    return tuple(receipts)


def accept_billing_proposal_pull(
    payload: object, database_url: str, tenant_reference: str
) -> dict[str, object]:
    """Run a Billing pull command for *tenant_reference* and return posted receipts."""
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
    receipts = accept_pulled_proposals(
        billing_base_url,
        database_url,
        tenant_reference,
        proposed_after=str(proposed_after) if proposed_after else None,
        cursor=str(cursor) if cursor else None,
        page_limit=page_limit,
    )
    return {"posting_receipts": list(receipts)}


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
    request = Request(
        f"{url}?{urlencode(query)}",
        headers={TENANT_HEADER: tenant_reference, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read()
    except HTTPError as error:
        raise _billing_http_error(error) from error
    except URLError as error:
        raise AccountingValidationError(
            "Billing journal-proposal pull could not be reached. "
            "Retry the Billing pull after Billing recovers."
        ) from error
    return _parse_billing_object(raw)


def _billing_http_error(error: HTTPError) -> AccountingValidationError:
    status = error.code
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
