# ADR 0017: HTTP outbox read and publish

**Status:** Accepted

## Decision

AIS exposes `lookup_outbox_events` / `GET /outbox-events?event_type_code=` and `publish_outbox_event` / `POST /outbox-events/{outbox_event_id}/publish` on the same stdlib HTTP surface as posting-receipt lookup. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. GET returns unpublished rows from the existing `accounting_integration.outbox_event` table (`published_at` IS NULL) for that tenant and `event_type_code` (`posting_receipt`, `period_close`, or `journal_reversal`). Item keys are `outbox_event_id`, `event_type_code`, `aggregate_reference`, `payload_reference`, `payload_hash`, and `created_at`. `payload_reference` is the receipt or snapshot URN persist already writes; AIS does not invent a payload blob. An empty unpublished set returns `outbox_events` []. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `created_at|outbox_event_id`. POST publish sets `published_at` on that tenant-owned row; replay is idempotent and writes no other rows. A tenant-header mismatch is rejected before the read or update and writes zero rows. Unknown `outbox_event_id` is 404. `POST /outbox-events` is 405. GET on the publish path is 405. `GET /posting-receipts` remains the receipt lookup.

## Consequences

Buyers can drain unpublished posting, reversal, and period-close events without polling only `GET /posting-receipts`. Outbox authority remains the existing transactional `outbox_event` row written in the same commit as post, reverse, or close.
