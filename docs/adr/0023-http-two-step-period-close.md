# ADR 0023: Two-step period close on POST /period-closes

**Status:** Accepted

## Decision

Controllers close a fiscal period in two steps on the existing `accept_period_close` / `POST /period-closes` command. The request body field is `period_status_code` (`soft_closed` or `hard_closed`). Omitting the field keeps today's default: `hard_closed`. Empty string or JSON null is 422 and does not silently hard-close. AIS does not add a second close route or a `close_type_code`.

`soft_closed` sets `fiscal_period.period_status_code` and writes a `period_close` outbox event. It does not persist `trial_balance_snapshot`. Ordinary new posts are rejected with the existing closed-period next action. Append-only reversal into that period remains allowed as a period-end adjusting entry. `GET /trial-balances` stays a live aggregation so a later reversing journal appears. Re-soft-close replays the same receipt keys (`snapshot_record_id` is empty; `snapshot_generated_at` is `period_closed_at`) and writes zero extra rows.

`hard_closed` keeps the ADR 0006 snapshot-and-lock: persist trial-balance snapshot lines, reject ordinary posts and reversals, and replay an already hard-closed period without a second snapshot. `open` → `hard_closed` remains the backward-compatible path. `soft_closed` → `hard_closed` is allowed and snapshots the live book once, including adjusting reversals. `hard_closed` → `soft_closed` is rejected (422) and writes zero rows; this slice does not reopen a hard-closed period.

The close receipt keeps the existing keys and includes `period_status_code` so the client can see `soft_closed` versus `hard_closed`. Cross-tenant close remains 403 and writes zero rows.

This two-step close follows the period-end adjusting practice in IAS 10: events after the reporting period that provide evidence of conditions that existed at period end are adjusting, and they are recorded before the books are locked (IFRS Foundation, 2022). Soft-close is that adjusting window; hard-close is the durable lock.

## Consequences

Controllers can stop ordinary Billing posts, book reversing adjustments, read a live trial balance, then hard-close once. ADR 0006's snapshot-on-every-close rule and ADR 0010's "closed period means snapshot TB" rule are superseded for `soft_closed` only. Hard-close, omit-field default, and idempotent hard re-close stay as they were.
