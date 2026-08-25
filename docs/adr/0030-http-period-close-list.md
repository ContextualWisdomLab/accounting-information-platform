# ADR 0030: HTTP period-close list

**Status:** Accepted

## Decision

AIS exposes `lookup_period_closes` and `GET /period-closes` on the same stdlib HTTP surface as `POST /period-closes`. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. Required `legal_entity_reference` scopes the list to that tenant entity. The read returns existing `accounting_reporting.trial_balance_snapshot` rows joined to `fiscal_period` and `accounting_book`, using the same close-receipt keys POST already returns (`tenant_reference`, `legal_entity_reference`, `accounting_book_reference`, `period_code`, `period_status_code`, `snapshot_record_id`, `snapshot_generated_at`, `source_journal_count`, `source_payload_hash`, and `replayed`). `replayed` is false because the list is the stored snapshot, not a reconstructed re-close. AIS does not invent a close-history table.

Soft-close writes no `trial_balance_snapshot`. The list therefore does not invent a soft-close receipt from live journals, `period_closed_at`, or outbox rows. Optional `fiscal_period_reference` keeps that period's stored snapshot; a missing period fails closed. Optional `period_status_code` is `soft_closed` or `hard_closed` and filters the joined period status; `soft_closed` returns `period_closes` [] because no snapshot exists. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `snapshot_generated_at|snapshot_record_id`. An empty history returns `period_closes` [] rather than 404. Filter keys appear on the document only when the query supplied them. `POST /period-closes` remains the ADR 0010 / ADR 0023 close command. A tenant-header mismatch is rejected before the read and writes zero rows.

Hard-close is the durable lock after the IAS 10 adjusting window (IFRS Foundation, 2022). Controllers and auditors retrieve that lock evidence from stored snapshot rows without SQL (American Institute of Certified Public Accountants, 2017).

## Consequences

An auditor can list which periods were hard-closed, when the snapshot was taken, and the frozen source hash, from the same rows POST already writes. Soft-close remains a command-time receipt only. The close command, trial-balance read, and idempotent re-close stay unchanged.
