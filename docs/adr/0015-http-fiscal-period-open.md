# ADR 0015: HTTP fiscal-period open

**Status:** Accepted

## Decision

AIS exposes `accept_period_open` / `POST /fiscal-periods` and `lookup_fiscal_period` / `GET /fiscal-periods?legal_entity_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as period close. Open inserts the existing `fiscal_period` row shape (`period_code`, `period_start_date`, `period_end_date`, `period_status_code=open`) on the tenant calendar, or replays an already-open period without a second row. A `hard_closed` or `soft_closed` period is not reopened. GET returns the persisted status and dates. Missing catalog facts are not invented. A tenant-header mismatch is rejected before a write.

## Consequences

Controllers can open the next fiscal period and then post or close without SQL. Chart, journal, and close authority stay on the existing tables. Cross-tenant open and period reads write zero rows.
