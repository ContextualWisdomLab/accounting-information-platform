# Accounting Information Platform Initial Design

## Context

CWL now has a Metering Billing Platform for usage, pricing, entitlement, invoice intent, payments, refunds, provider settlement, and commercial reconciliation. Extending that repository into a general ledger would collapse distinct authorities and make provider events dictate accounting policy.

## Approaches considered

### Extend Metering Billing Platform

Fastest first implementation, but billing state, provider state, accounting books, close, reporting, and revenue policy become one high-coupling system. Rejected.

### Use an external ERP as the only accounting authority

Reduces initial implementation, but makes CWL source provenance, policy versioning, and cross-product reconciliation depend on one vendor's objects. The platform still needs a provider-neutral accounting contract and control plane. Rejected as the core architecture; retained as a future posting/export adapter option.

### Separate accounting authority with proposal and receipt contracts

Recommended. Billing owns commercial facts and submits semantic proposals. Accounting resolves policy and returns authoritative receipts. External ERP, tax, bank, and reporting systems remain replaceable adapters.

## Initial vertical

```text
balanced proposal
-> identity and payload-hash validation
-> idempotency decision
-> tenant/entity/book/period/currency/account mapping
-> immutable posted journal
-> posting receipt and outbox
-> trial balance
-> append-only reversal
```

## Scope

The first PR implements a dependency-free reference core, normalized PostgreSQL foundation, external schemas, CI, tests, and governance documentation. It deliberately excludes HTTP service, live PostgreSQL adapter, foreign exchange, revenue schedules, bank ingestion, financial statements, consolidation, and tax calculation.

## Invariants

- proposals cannot claim `posted`;
- only accounting receipts can claim a posting outcome;
- exact replay creates one journal;
- conflicting idempotency reuse fails closed;
- all journals balance;
- closed periods reject ordinary posting;
- source account roles require policy mapping;
- posted journals are immutable;
- reversal preserves both original and corrective evidence;
- trial balance ties to the selected journal population;
- all authoritative records are tenant-scoped.
