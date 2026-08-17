# Operability Baseline

## Persistence adapter

Set `ACCOUNTING_DATABASE_URL` to a PostgreSQL 18 database, apply `database/migrations/0001_accounting_foundation.sql`, then post and close through `PostgresPostingLedger` or `accept_journal_proposal`. For HTTP, set `ACCOUNTING_TENANT_REFERENCE` and bind `0.0.0.0:$PORT` with `run_journal_proposal_server`. POST `/journal-proposals` with `X-CWL-Tenant-Reference` only; that header scopes tenant identity and is not a general credential. If the header or payload tenant does not match this process, do not post—send the proposal to that tenant's AIS endpoint, then retry. After a receipt is posted, GET `/posting-receipts?idempotency_key=` with the same tenant header to read the persisted `accounting_posting_receipt`. If the header does not match this process, do not look up—send the lookup to that tenant's AIS endpoint, then retry. If no receipt exists for that Billing key, do not invent one—accept the proposal, then retry the receipt read. Ingest Billing JSON through `ingest_journal_proposal`; if the payload is a Billing operational reject or `proposal_status` is `draft` or `rejected`, do not ingest it—ask Billing for a `validated` proposal, then retry. Post that proposal with `post_proposal` so AIS resolves chart accounts from `account_role_mapping`. If posting is rejected, the receipt is not written; create the named catalog or mapping row, or open the fiscal period, then retry. Do not invent chart-account codes in Billing or in the operator request. If close is rejected, restore the named catalog or snapshot row, then retry the close. Re-closing a hard-closed period replays the existing snapshot and writes no second close event. After AIS emits `posting_receipt`, do not expect Billing to flip the proposal to `posted`. Replay of the same Billing idempotency key returns the original receipt. POST `/period-closes` with the same tenant header to snapshot the book and set `soft_closed` or `hard_closed`; replay returns the same close receipt and writes no second snapshot. If close is rejected, restore the named catalog or snapshot row, then retry the close. GET `/trial-balances?legal_entity_reference=&book_reference=&fiscal_period_reference=` to read locked snapshot totals for a closed period or live aggregation for an open period. If the header does not match this process, do not close or read—send the request to that tenant's AIS endpoint, then retry. If the book or period is missing, do not invent balances—create the named catalog row, then retry the trial-balance read. GET `/healthz` is an ops probe and returns `{"status":"ok"}` without a tenant header or accounting data. Set `BILLING_BASE_URL` to the Billing origin; do not hardcode a host. POST `/billing-proposal-pulls` with the same tenant header and optional `billing_base_url`, `proposed_after`, `cursor`, and `page_limit` to pull Billing `GET /v1/journal-proposals` pages (`proposal_status=validated` only; envelope keys `journal_proposals` and `next_cursor`) and post each item. If `BILLING_BASE_URL` is missing or Billing returns 422 or 5xx, do not write AIS rows—set the Billing origin or ask Billing to correct the tenant header and `tenant_reference` query, then retry the pull. A Billing 404 on `GET /v1/journal-proposals/{proposal_id}` is unknown or cross-tenant; do not retry as another tenant. After AIS emits `posting_receipt`, do not call Billing to mark the proposal `exported`, `posted`, or consumed. Replay of the same Billing page returns the original receipts and writes no second journal.

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
