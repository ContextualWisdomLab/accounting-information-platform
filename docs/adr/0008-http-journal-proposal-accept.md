# ADR 0008: HTTP journal-proposal accept boundary

**Status:** Accepted

## Decision

AIS exposes `accept_journal_proposal` and a stdlib `POST /journal-proposals` endpoint that ingest a Billing `accounting_journal_proposal` and return an `accounting_posting_receipt`. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. A mismatch with the process tenant binding or the payload `tenant_reference` is rejected before posting. Replay of the same Billing idempotency key returns the original receipt document.

## Consequences

Billing can hand a validated proposal to AIS without embedding a Python library call. Chart accounts and policy versions still come from the AIS catalog. Cross-tenant posts and non-ingestible Billing rows write zero journals. The in-memory `PostingLedger` remains the reference oracle and is not an HTTP server.
