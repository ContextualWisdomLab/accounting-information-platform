# Technical Requirements Document

## Architecture style

Begin as a contract-first modular monolith. The accounting domain core is isolated from HTTP, event transport, and persistence adapters. PostgreSQL is the durable authority; the current in-memory ledger is an executable reference oracle for adapter conformance tests.

## Transaction boundary

A durable posting transaction will eventually perform the following atomically:

```text
proposal receipt
+ idempotency and payload-hash decision
+ policy and period resolution
+ general journal
+ journal lines
+ source references
+ posting receipt
+ transactional outbox event
```

No consumer receives a `posted` receipt unless that transaction commits.

Each multithreaded HTTP request uses an independent PostgreSQL transaction.
New sessions set bounded lock and idle-transaction timeouts. State-changing
commands acquire transaction-level advisory locks keyed by tenant and command
scope; posting/reversal re-read the selected period under a shared period lock,
while close selection uses a row lock.
Migration 0006 adds tenant-leading indexes to high-write evidence tables and
records the primary/foreign-key constraints that a future hash-by-tenant/time
partition migration must preserve.

## Precision

- API and event amounts use canonical decimal strings.
- PostgreSQL uses `numeric(38, 6)` in the first milestone.
- Python uses `decimal.Decimal` after strict canonical parsing.
- Binary floating-point types are forbidden in accounting arithmetic.
- Foreign-exchange accounting is explicitly rejected until rate source, rate type, date, rounding, remeasurement, and translation policy are implemented.

## Temporal model

- `transaction_date`: economic transaction date.
- `accounting_date`: requested ledger date.
- `valid_from` and `valid_to`: real-world policy or master-data validity.
- `recorded_at`: system knowledge time.
- `posted_at`: authoritative posting completion.
- `reversed_at`: reversal lineage creation.
- `period_closed_at`: fiscal close control time.

## Contracts

- JSON Schema Draft 2020-12 for payload contracts.
- UUIDv7 for new PostgreSQL record identifiers.
- SHA-256 source hashes for immutable evidence identity.
- Idempotency keys for every state-changing external command.
- CloudEvents-compatible outbox events in the service milestone.

## Security

- Tenant scope is carried on every authoritative record.
- Composite foreign keys prevent cross-tenant relation construction.
- PostgreSQL row-level security uses an explicit session tenant context.
- Source payload bodies remain outside journal tables; only immutable references and hashes are stored.
