# ADR 0027: HTTP audit-event history

**Status:** Accepted

## Decision

AIS exposes `lookup_audit_events` and `GET /audit-events` on the same stdlib HTTP surface as the outbox drain. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `accounting_integration.outbox_event` rows for that tenant, including rows whose `published_at` is already set. Item keys reuse the outbox list (`outbox_event_id`, `event_type_code`, `aggregate_reference`, `payload_reference`, `payload_hash`, `created_at`) and add `published_at` so auditors can see drain state. `payload_reference` remains the receipt or snapshot URN persist already writes; AIS does not invent a payload blob or a second audit table. Optional `event_type_code` is the same closed set already written by persist (`posting_receipt`, `period_close`, `journal_reversal`); omitting it returns all types and omits that document key. The table has no `legal_entity_reference` column, so the list does not invent that filter. Pages reuse the outbox cursor (`page_limit` default 50, maximum 100, `next_cursor` on `created_at|outbox_event_id`). An empty history returns `audit_events` [] rather than 404. The read never sets `published_at`. `GET /outbox-events` remains unpublished-only (`published_at` IS NULL). `POST /outbox-events/{outbox_event_id}/publish` remains the drain path. `POST /audit-events` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

This keeps SOC 2 / CSAP audit-trail evidence on the same append-only outbox rows that post, reverse, and close already write in one commit (American Institute of Certified Public Accountants, 2017).

## Consequences

Auditors can reconstruct what AIS posted, reversed, and closed after the drain worker has published those rows. Publication remains a separate, idempotent mark on the same row. Closing journals do not invent extra outbox event types.
