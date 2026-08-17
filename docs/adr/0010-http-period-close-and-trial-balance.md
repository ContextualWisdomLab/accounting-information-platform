# ADR 0010: HTTP period close and snapshot trial-balance read

**Status:** Accepted

## Decision

AIS exposes `accept_period_close` / `POST /period-closes` and `lookup_trial_balance` / `GET /trial-balances` on the same stdlib HTTP surface as journal-proposal accept. Close calls `PostgresPostingLedger.close_fiscal_period` in one transaction (trial-balance snapshot plus period status) and returns `PeriodCloseReceipt` as JSON. Replay of an already-closed period returns the same snapshot identity. A closed-period trial-balance read returns the persisted snapshot; an open period returns a live aggregation through period end. Missing catalog or period facts fail closed and do not invent a close or balances. `GET /healthz` is an unauthenticated ops probe and returns no accounting data.

## Consequences

Controllers can close a fiscal period and read locked books without an in-process Python import. Cross-tenant close and trial-balance requests are rejected before a write. The purpose-limited `X-CWL-Tenant-Reference` header remains the only request identity header on accounting routes.
