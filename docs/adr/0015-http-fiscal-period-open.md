# ADR 0015: HTTP fiscal-period open

**Status:** Accepted

## Decision

AIS exposes `accept_period_open` / `POST /fiscal-periods` and `lookup_fiscal_period` / `GET /fiscal-periods?legal_entity_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as period close. Open requires a tenant-scoped `idempotency_key` and canonical immutable `source_payload_hash`. The command records durable `fiscal_period_open_command` evidence atomically with a newly inserted `fiscal_period` row, or records the command against an already-open matching period. The same tenant/key/payload replays the recorded result even after the period later closes; reuse of the key with changed entity, period, requested dates, or source hash fails closed. A new command cannot reopen a `hard_closed` or `soft_closed` period. GET returns the persisted status and dates. Missing catalog facts are not invented. A tenant-header mismatch is rejected before a write.

## Consequences

Controllers can open the next fiscal period and then post or close without SQL. Durable command evidence makes retries auditable without treating the mutable period status as the command identity. Chart, journal, and close authority stay on the existing tables. Cross-tenant open and period reads write zero rows.
