# ADR 0008: HTTP journal-proposal accept boundary

**Status:** Accepted

## Decision

AIS exposes `accept_journal_proposal` and a stdlib `POST /journal-proposals` endpoint that ingest a Billing `accounting_journal_proposal` and return an `accounting_posting_receipt`. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. A mismatch with the process tenant binding or the payload `tenant_reference` is rejected before posting. Replay of the same Billing idempotency key returns the original receipt document.

Routing uses the URL path without the query string, so `POST /journal-proposals?trace=1` still accepts. Contract validation (`AccountingValidationError`) is HTTP 422 with an operator next-action sentence and never 500. Request bodies larger than 1 MiB are 413 and are not treated as an empty body. `proposal_contract_version` and line `line_number` must be JSON integers (not bool, not `"one"`). Line amounts must be canonical decimal strings.

## Consequences

Billing can hand a validated proposal to AIS without embedding a Python library call. Chart accounts and policy versions still come from the AIS catalog. Cross-tenant posts, non-ingestible Billing rows, malformed lines, and oversized bodies write zero journals. The in-memory `PostingLedger` remains the reference oracle and is not an HTTP server.
