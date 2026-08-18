# ADR 0016: HTTP period journal list

**Status:** Accepted

## Decision

AIS exposes `lookup_period_journals` and `GET /journals?legal_entity_reference=&fiscal_period_reference=&book_reference=` on the same stdlib HTTP surface as single-journal inquiry. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `general_journal` rows for that tenant, legal entity, book, and fiscal period (`journal_reference`, stored `idempotency_key`, `journal_status_code`, `accounting_date`, `line_count`, and `reversal_of_journal_reference` when the row is a reversing journal). It does not invent a list table. An empty period returns `journals` [] rather than 404. Missing legal entity, book, or period fails closed. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `accounting_date|journal_reference`. `GET /journals?idempotency_key=` and `GET /journals?journal_reference=` remain the ADR 0014 single-journal line inquiry. `POST /journals` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

## Consequences

Controllers can list a period's posted and reversing journals without knowing every Billing idempotency key. Line detail stays on the single-journal GET. Journal authority remains the existing append-only `general_journal` population.
