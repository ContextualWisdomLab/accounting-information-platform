# Reconciliation approval snapshot binding plan

> **For agentic workers:** Use `superpowers:executing-plans` and keep each production change behind an observed RED test.

**Goal:** Bind durable reconciliation approval evidence to a database-computed, exact snapshot of the candidate and allocation rows that the approval authorizes.

**Scope:** Extend the next migration after the exact PR #29 head, update the migration loader and current data-model/operability/ADR/changelog traceability, and prove the stale-allocation race fails closed on PostgreSQL 18.

## Tasks

- [x] Add a static RED contract for migration 0016's database-owned snapshot hash/version and locking boundary.
- [x] Add migration 0016 and loader support with the existing approval/state controls preserved.
- [x] Add PostgreSQL RED regressions that attempt to change proposed allocations or retarget an approved match after approval evidence and prove the mutations fail closed.
- [x] Implement canonical SHA-256 snapshot computation, approval binding, and shared advisory serialization for approval/allocation transitions.
- [x] Run focused, full, coverage, repository-contract, and migration-chain checks; update exact-head evidence and documentation.
