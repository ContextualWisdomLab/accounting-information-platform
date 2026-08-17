# ADR 0026: HTTP legal-entity list

**Status:** Accepted

## Decision

AIS exposes `lookup_legal_entities` and `GET /legal-entities` on the same stdlib HTTP surface as the accounting-book catalog. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `accounting_core.legal_entity_record` rows for that tenant. `legal_entity_code` is the durable entity identity already returned as `legal_entity_reference` on books, periods, trial balances, and statements. `entity_name` is also returned because that column exists. The list does not invent an entity-code alias, a list table, a required query filter, or paging; the catalog is small and ordered by `legal_entity_code`. Only current rows (`valid_to` IS NULL) are returned. An empty tenant returns `legal_entities` [] rather than 404. `POST /legal-entities` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

## Consequences

Controllers can discover `legal_entity_reference` without SQL before calling `GET /accounting-books`, `GET /fiscal-periods`, `GET /trial-balances`, or `GET /financial-statements`. Entity authority remains the existing `legal_entity_record` population.
