# ADR 0018: HTTP fiscal-period list

**Status:** Accepted

## Decision

AIS exposes `lookup_fiscal_periods` and `GET /fiscal-periods?legal_entity_reference=` on the same stdlib HTTP surface as period open and single-period GET. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `fiscal_period` rows on the tenant calendar already used by open and close (`fiscal_period_reference`, `period_code`, `period_start_date`, `period_end_date`, and `period_status_code` of `open`, `soft_closed`, or `hard_closed`). It does not invent a list table. An empty calendar returns `fiscal_periods` [] rather than 404. A missing legal entity fails closed. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `period_start_date|period_code`. `GET /fiscal-periods?legal_entity_reference=&fiscal_period_reference=` remains the ADR 0015 single-period status read. `POST /fiscal-periods` remains the period-open command. A tenant-header mismatch is rejected before the read and writes zero rows.

## Consequences

Controllers can list an entity's fiscal periods without knowing every period code. Single-period GET and POST open stay unchanged. Period authority remains the existing `fiscal_period` population.
