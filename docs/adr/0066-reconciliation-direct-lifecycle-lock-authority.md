# ADR 0066: Direct reconciliation lifecycle authority requires server lock proof

- Status: Proposed
- Date: 2026-09-03
- Bounded context: Bank Reconciliation / Evidence and Audit
- Depends on: ADR 0060 reconciliation-run lifecycle authority
- Follow-up: issue #44 for the least-privilege database capability

## Problem

The supported reconciliation lifecycle command acquires a tenant/run session advisory lock, commits that acquisition, starts a fresh PostgreSQL `REPEATABLE READ` transaction, and then acquires the matching transaction advisory lock. Before migration 0027, a privileged raw INSERT into `reconciliation_run_transition_command` could enter the database-owned snapshot trigger before the later transition-hash trigger acquired the transaction lock. A lock wait inside that later trigger cannot recreate a statement or transaction snapshot that already exists.

The result was a direct-database freshness gap: PostgreSQL could compute the transition snapshot itself yet compute it from a predecessor view of eligibility-changing reconciliation evidence.

## Constraints

The fix must preserve the existing database-owned statement/book populations, exact Decimal book-to-bank equation, immutable reviewed evidence, maker-checker exception authority, transition idempotency, outbox pairing and the separation between reconciliation evidence and General Ledger/period-close authority. Direct foreign database access remains prohibited; this ADR governs only AIS's own database bypass boundary.

## Decision

Migration `0027_reconciliation_lifecycle_session_lock_authority.sql` adds a first-sorting `BEFORE INSERT` prerequisite trigger on `accounting_core.reconciliation_run_transition_command`. PostgreSQL trigger lexical ordering places it before `accounting_reconciliation_transition_database_authority_guard`, so unsafe DML is rejected before statement, journal, allocation, approval or exception populations are read for transition authority.

The prerequisite verifies three facts on the current backend and exact tenant/run advisory key:

1. the backend owns a session-level advisory hold, proved with `pg_advisory_unlock(...)` rather than inferred from `pg_locks` alone;
2. after decrementing that session hold, the same backend still owns the exact granted advisory key in `pg_locks`, proving a transaction-level hold keeps the serialization boundary closed while the session hold is immediately restored; and
3. the authority transaction is `REPEATABLE READ`.

The supported application sequence remains stronger than this live-state proof: session lock → commit → fresh `REPEATABLE READ` → transaction lock → authority reads/writes → commit/rollback → explicit session unlock. Positive direct PostgreSQL acceptance that intentionally exercises the table-level authority must follow that same sequence. A raw path with no session lock or only a transaction lock fails with `reconciliation_lifecycle_session_lock_required` before the database snapshot function runs.

`pg_advisory_unlock` is used as a lock-type probe because PostgreSQL session-level and transaction-level advisory locks use the same lock namespace and `pg_locks` does not identify the acquisition API. The transaction-level hold remains while the session hold is decremented and restored, preventing another backend from entering the exact key during the probe.

## Alternatives

**Keep the later transaction lock only.** Rejected because waiting later in a statement or repeatable-read transaction cannot refresh its already-established snapshot.

**Inspect only `pg_locks`.** Rejected because the view shows the advisory key and backend but not whether the hold came from the session-level or transaction-level API.

**Use a caller GUC to attest safe ordering.** Rejected because a direct SQL caller could forge the marker independently of lock-manager state.

**Use `READ COMMITTED` for finalization.** Rejected because the multiple authority queries could observe different statement snapshots.

**Remove all database-side transition authority.** Not selected in this slice. The current architecture deliberately keeps PostgreSQL as an independent invariant boundary. The later least-privilege capability should nevertheless remove raw transition/status/outbox DML from application identities and expose only named commands.

## Risk and effect

The trigger verifies live session-lock, transaction-lock and isolation state. PostgreSQL does not expose historical session-lock acquisition time, so the complete session-lock commit → fresh transaction order remains a protocol requirement enforced by the supported command and positive database acceptance tests. A broadly privileged principal could deliberately reproduce both lock forms in another order; such broad raw DML is therefore outside the intended runtime capability and must be removed by issue #44 rather than treated as an ordinary product API.

This limitation is explicit rather than hidden as a compliance claim. Migration 0027 is defense in depth for accidental/raw bypasses while the named command remains the product authority path.

## Verification

Exact-head acceptance must include real PostgreSQL tests that reject raw lifecycle transition under `READ COMMITTED`, reject raw `REPEATABLE READ` transition without the session lock, reject a transaction-lock-only substitute, and preserve the existing safe direct-database tests for caller-identity replacement, timezone-independent database hashing and untied bridge rejection after those tests enter the documented lock protocol. Repository contracts must also prove that the prerequisite trigger sorts before the database-authority trigger and that the canonical installer includes migration 0027.

The complete candidate still requires exact 100% owned production statement/branch coverage, public docstrings, repository contracts, SAST/security/dependency review, reproducible package/SBOM/provenance, current-head review, migration/recovery evidence and live ruleset admission before integration.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking—Advisory locks*. https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: The pg_locks view*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
