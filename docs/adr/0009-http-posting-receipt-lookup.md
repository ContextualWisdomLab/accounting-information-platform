# ADR 0009: HTTP posting-receipt lookup

**Status:** Accepted

## Decision

AIS exposes `lookup_published_receipt` and `GET /posting-receipts?idempotency_key=` on the same stdlib HTTP surface as proposal accept. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. Lookup returns the persisted `accounting_posting_receipt` for that tenant and Billing idempotency key. A missing receipt is not invented. A tenant-header mismatch is rejected before the read.

## Consequences

Billing can retrieve a later posting receipt for an invoice_draft or cash_receipt key without an in-process Python import. Cross-tenant and unknown-key lookups write zero journals. Replay of `POST /journal-proposals` and a later GET return the same receipt document.
