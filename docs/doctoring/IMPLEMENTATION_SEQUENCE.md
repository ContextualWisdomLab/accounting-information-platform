# Implementation Sequence

The current foundation proves posting invariants in a dependency-free reference core, closed JSON Schema contracts, and a checked-in PostgreSQL 18.4 schema. It does not yet run a live persistence adapter or HTTP service.

## Next customer-visible increment

Implement the durable PostgreSQL proposal-intake transaction as one commit boundary:

1. idempotent proposal receipt;
2. source-hash conflict detection;
3. open-period and policy resolution;
4. general journal and journal lines;
5. posting receipt;
6. transactional outbox event.

No consumer receives a `posted` receipt unless that transaction commits. The PostgreSQL adapter must pass the same behavior fixtures as `accounting_information_platform.core` before becoming authoritative.

## Later increments

Read-only API and operator hold queue; billing integration and source-to-posting reconciliation; revenue and settlement accounting; then cash, ISO 20022 adapters, close, multi-currency, reporting projections, and consolidation. Service boundaries must not introduce direct cross-service SQL.
