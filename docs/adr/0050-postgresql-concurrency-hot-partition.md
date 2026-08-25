# ADR 0050: PostgreSQL concurrency and hot-partition readiness

**Status:** Accepted

## Context

The HTTP listener is multithreaded, so independent requests can reach the same
tenant, idempotency key, fiscal period, reversal, outbox, or tax-evidence rows
at the same time. PostgreSQL must serialize state-changing commands without
turning a retry race into a duplicate journal, a second close snapshot, or an
unhandled indefinite lock wait.

The append-only journal, proposal, receipt, outbox, reversal, and HomeTax
tables are also the high-write populations most likely to become hot for one
tenant. Their existing tenant-scoped uniqueness and composite foreign keys are
authoritative contracts. PostgreSQL declarative partitioning has a material
constraint here: a partitioned table's unique or primary-key columns must
include the partition key. Partitioning these tables without redesigning
their composite keys and every dependent foreign key would weaken or silently
change the accounting identity boundary.

## Decision

1. Every newly opened application PostgreSQL session sets
   `lock_timeout` to five seconds and `idle_in_transaction_session_timeout` to
   sixty seconds. A lock timeout rolls back the transaction and writes no
   accounting fact; the caller receives a fail-closed database error.
2. State-changing command paths acquire transaction-level advisory locks using
   the configured tenant and a deterministic command scope. The lock is held
   only until commit or rollback. Proposal, adjusting-journal, reversal, and
   HomeTax commands use command-specific scopes; posting, period-open,
   period-close, and reversal also use the shared `period:{period_code}` scope
   where a fiscal period is known.
3. Posting and reversal first resolve a period, acquire its shared advisory
   lock, and re-read its status. Close acquires that same period lock and then
   locks the exact period row with `FOR UPDATE` before evaluating the
   repeatable-read close package. This serializes close against a concurrent
   state-changing write for the same period while allowing unrelated tenants
   and periods to proceed without requiring UPDATE privilege for the runtime
   login.
4. Migration `0006_concurrency_hot_partition.sql` adds tenant-leading
   multicolumn indexes for high-write evidence and a partial pending-outbox
   index. These indexes bound tenant and time scans now and are the compatible
   access paths for a future hash-by-tenant plus time partition layout.
5. Physical partitioning is a separate scale migration. Before adopting it,
   include the partition key in every affected primary/unique key and
   tenant-scoped foreign key, rehearse attach/backfill/pruning, and prove RLS,
   idempotency, reversal lineage, and outbox ordering on the partitioned
   tables. No partition is introduced by this milestone merely for an
   unmeasured performance claim.

## Consequences

Same-tenant command retries are serialized at the database boundary rather
than relying on Python process memory. Lock waits are bounded, and PostgreSQL
cleans transaction-level advisory locks when the transaction ends. Tenant-first
indexes reduce the scan population for current reads and preserve a migration
path toward partition pruning. The current normalized schema remains
third-normal-form and keeps its cross-tenant composite foreign-key guarantees.

This is readiness evidence, not a claim that a production partition benchmark,
read/write split, CSAP certification, or SOC 2 attestation has been completed.
Those require production-shaped load, lock-wait, backup/restore, and control
testing before release.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation:
Advisory locks*. https://www.postgresql.org/docs/18/functions-admin.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation:
Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation:
Table partitioning*. https://www.postgresql.org/docs/18/ddl-partitioning.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation:
Multicolumn indexes*. https://www.postgresql.org/docs/18/indexes-multicolumn.html
