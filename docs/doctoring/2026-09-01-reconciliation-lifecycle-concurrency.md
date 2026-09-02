# Doctoring record: reconciliation lifecycle concurrency and status authority

**Date:** 2026-09-01  
**Updated:** 2026-09-02  
**Scope:** stacked reconciliation lifecycle candidate on `accounting-information-platform`

## Research question

What concurrency and transaction contract lets a reconciliation run move from `evaluating`/`review_required` to `reconciled` without approving stale, partially reviewed, replay-inconsistent, cross-aggregate-reassigned, or internally mixed bank/book evidence?

## PostgreSQL finding

PostgreSQL 18 gives materially different snapshot semantics to `READ COMMITTED` and `REPEATABLE READ`. `READ COMMITTED` obtains a new snapshot for each command. `REPEATABLE READ` keeps one transaction snapshot established by the first non-transaction-control statement. A transaction advisory lock is acquired through a SQL statement, so `SELECT pg_advisory_xact_lock(...)` can establish a repeatable-read snapshot **before** it waits.

That makes the naïve sequence “start `REPEATABLE READ`, then take the transaction advisory lock” unsafe for a waiting finalizer: after the preceding reconciliation writer commits and releases the lock, the waiter can still retain a snapshot that predates that commit.

Changing finalization to `READ COMMITTED` fixes that particular stale-wait problem but introduces another accounting defect. `_load_review_control_state()`, exception-resolution reads, `_database_owned_close_projection_evidence()`, opening-command provenance, transition insertion, status update, and outbox publication span multiple SQL commands. The bridge helper itself reads statement scope, balances, entries, posted cash journals, and allocation populations in separate statements. Bank-statement and General Ledger facts are append-only but are not all required to acquire the reconciliation lifecycle lock. Under `READ COMMITTED`, a source fact committed between those statements can therefore enter only the later part of the authority calculation.

The accepted transaction model is consequently:

1. On one PostgreSQL session, acquire the **session-level** advisory lock using the exact run lifecycle key and commit the preliminary transaction. The lock survives that commit.
2. Only after the session lock has been granted, start a fresh `REPEATABLE READ` transaction on the same session.
3. Reacquire the same transaction-level lifecycle lock reentrantly, preserving the lock contract used by database triggers and existing reconciliation writers.
4. Read run, review, exception, bank-statement, posted-journal, allocation, and opening-command evidence and write transition/status/outbox inside that one repeatable-read transaction.
5. Commit the authority transaction before releasing the session advisory lock.

This gives both required properties: evidence committed by a preceding guarded lifecycle writer is visible because the repeatable-read transaction starts after lock grant; later source-population commits cannot create a mixed finalization snapshot because every authority query sees the same transaction snapshot.

### References (APA 7th)

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html

## Falsifiable traceability

| Requirement | Implementation boundary | RED/GREEN evidence |
| --- | --- | --- |
| Predecessor commit visible after lock wait | `_coherent_lifecycle_session()` obtains session lock before fresh repeatable-read transaction | `test_reconciliation_lifecycle_lock_wait_postgres.py`: resolution writer holds the shared run lock, finalizer becomes a real advisory-lock waiter, writer commits, finalizer must observe terminal resolution and reconcile |
| One coherent source/review snapshot | Fresh `REPEATABLE READ` transaction begins only after session-lock grant | `test_reconciliation_lifecycle_source_snapshot_postgres.py`: after review state is read, a separate connection commits otherwise eligible bank entries; current finalization must retain the pre-insert statement-population identity |
| Session/transaction lock order | session lock → commit → `SET TRANSACTION ... REPEATABLE READ` → transaction lifecycle lock → authority reads | focused `test_reconciliation_lifecycle_snapshot_freshness.py` plus unit happy/error paths |
| Aggregate initial state | migration lifecycle trigger accepts only `evaluating` inserts | real PostgreSQL raw terminal-state insertion rejection |
| Legal status edge | run row lock + lifecycle trigger permits only named `reconciled` transition | raw SQL status changes without named command fail |
| Aggregate membership | review evidence cannot change tenant/run ownership | migration contract + cross-run PostgreSQL rejection |
| Review completeness | proposed or decision-inconsistent match state fails closed | lifecycle unit/PostgreSQL coverage |
| Maker-checker exception authority | terminal exception requires matching immutable resolution command | lifecycle and exception-resolution unit/PostgreSQL coverage |
| Exact monetary authority | `_database_owned_close_projection_evidence()` derives statement/book populations and Decimal bridge from PostgreSQL | close-projection PostgreSQL tests and transition snapshot binding |
| Replay provenance | transition row persists exact statement/book population identities | exact replay returns persisted identities without rebuilding bridge |
| Atomic publication | transition command, run status, and outbox event share the authority transaction | PostgreSQL lifecycle acceptance |

## Causal repair history

### 1. Transaction-lock-first repeatable read

The first implementation used `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` followed by the transaction-level lifecycle advisory lock. Review demonstrated that the lock statement itself can establish the transaction snapshot before waiting. This could make a waiter reject a resolution that had already committed before lock grant.

### 2. Read-committed experiment

A focused RED reproduced the stale-wait premise and an intermediate repair changed lifecycle finalization to `READ COMMITTED`. The lock-wait visibility issue was removed, but current-head review correctly identified that sequential bank/review/book authority reads would no longer share one snapshot. Source inspection confirmed that the close bridge reads immutable-but-appendable bank evidence and posted-journal populations in multiple SQL statements and that those source writers do not all share the run lifecycle lock. The read-committed repair was therefore not accepted as the final concurrency model.

### 3. Session-lock-before-repeatable-read repair

A second RED inserts two net-zero but population-changing bank entries after review state has been read. Under `READ COMMITTED`, later bridge queries can see those rows and produce a source-population identity that did not exist when review state was read. The narrow repair acquires a session-level run lock, commits the preliminary lock-acquisition transaction, then opens a fresh repeatable-read transaction on that same session and keeps the session lock through the finalization commit. The ordinary transaction-level lifecycle lock is reacquired reentrantly so the existing database/app lock discipline remains intact.

This repair does **not** require bank-statement ingestion or General Ledger posting to take reconciliation-specific locks and does not couple those bounded contexts. Their append-only facts remain independently owned; finalization obtains consistency through PostgreSQL snapshot semantics.

## Other authority repairs retained on this stack

- Run-scope currency comes from the locked `reconciliation_run` row rather than an incidental close-projection object field.
- PostgreSQL rejects a non-`evaluating` initial run and rejects unnamed changed lifecycle targets.
- Exact replay persists and returns statement/book population references instead of reconstructing a later bridge.
- Candidate/match/allocation/approval/exception aggregate membership is immutable across tenant/run scope.
- Terminal exception state is valid lifecycle authority only with matching immutable maker-checker resolution-command evidence.

## Deliberate limitation

PostgreSQL independently enforces lifecycle state, aggregate membership, command immutability, reconciliation review/exception eligibility, and persistence of source-population identities. The complete `reconciliation_snapshot_hash` remains service-derived from database-owned facts observed in the coherent repeatable-read snapshot; PostgreSQL binds that digest into immutable command evidence but does not independently recompute every monetary bridge component in SQL. No compliance or certification claim is implied.

## Migration sequencing

Migration `0019_reconciliation_run_command_evidence.sql` remains unreleased on the dependency-root stack and migration `0020_reconciliation_exception_resolution_command.sql` remains stacked above it. The child must integrate through `#47 -> #43 -> #29` before those migrations reach protected `develop`. If protected history lands first, subsequent schema changes must use forward migrations; an applied migration must not be rewritten.

## Required current-head evidence

The branch is not merge-ready until one unchanged exact head passes the focused unit contracts, both real PostgreSQL concurrency tests, the complete PostgreSQL suite, exact 100% owned production statement/branch coverage, public-docstring/repository contracts, SAST/security/dependency gates, reproducibility/SBOM/provenance gates, and qualifying independent review. Queued, absent, predecessor-head, model-only, or status-only evidence is non-passing.
