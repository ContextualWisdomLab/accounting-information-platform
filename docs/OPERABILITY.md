# Operability Baseline

## Persistence adapter

Set `ACCOUNTING_DATABASE_URL` to a PostgreSQL 18 database, apply `database/migrations/0001_accounting_foundation.sql`, then post and close through `PostgresPostingLedger`. Ingest Billing JSON through `ingest_journal_proposal`; if the payload is a Billing operational reject or `proposal_status` is `draft` or `rejected`, do not ingest it—ask Billing for a `validated` proposal, then retry. Post that proposal with `post_proposal` so AIS resolves chart accounts from `account_role_mapping`. If posting is rejected, the receipt is not written; create the named catalog or mapping row, or open the fiscal period, then retry. Do not invent chart-account codes in Billing or in the operator request. If close is rejected, restore the named catalog or snapshot row, then retry the close. Re-closing a hard-closed period replays the existing snapshot and writes no second close event. After AIS emits `posting_receipt`, do not expect Billing to flip the proposal to `posted`.

## Initial service objectives

- Proposal intake preserves every accepted source hash and idempotency decision.
- Posting and outbox publication are atomic.
- Trial-balance totals tie exactly to the selected journal population.
- No ordinary posting enters a soft-closed or hard-closed period.
- Period close persists a trial-balance snapshot for the book and period in the same transaction that changes `period_status_code`.
- Every receipt is traceable to policy, rule, mapping, fiscal period, journal, and source proposal versions.

## Telemetry

Record OpenTelemetry-compatible traces and metrics for proposal validation, idempotent replay, policy resolution, hold and rejection reason, posting latency, outbox lag, reversal, period close, and trial-balance generation. Never place account secrets, raw PII, or complete source payloads in telemetry.

## Recovery

- PostgreSQL point-in-time recovery and object-store versioning.
- Periodic restore rehearsal into an isolated environment.
- Journal and receipt hash reconciliation after restore.
- Outbox replay by event identity without duplicate posting.
- No recovery procedure rewrites a posted journal; corrections retain reversal lineage.

## Incident response

High-severity events include duplicate posting, unbalanced persisted journal, cross-tenant access, closed-period posting, missing source lineage, trial-balance mismatch, or receipt without committed journal. The immediate action is to stop the affected posting route, preserve evidence, reconcile the population, and issue explicit reversals or compensating entries after controller approval.
