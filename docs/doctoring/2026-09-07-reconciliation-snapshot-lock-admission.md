# Doctoring record: reconciliation snapshot lock admission

**Date:** 2026-09-07  
**Scope:** Bank Reconciliation lifecycle authority, PR #43

## Finding

The prior lifecycle sequence set `REPEATABLE READ` and then executed `SELECT pg_advisory_xact_lock(...)` before reading reconciliation evidence. That ordering was intended to acquire the aggregate lock before the MVCC snapshot, but PostgreSQL defines a repeatable-read snapshot at the first query or data-modification statement. `pg_advisory_xact_lock` is invoked by `SELECT`, and it can wait for another session. A waiting transition could therefore freeze a snapshot before the competing evidence writer committed, acquire the lock later, and continue validating stale review/source facts.

This record supersedes the earlier wording in `2026-09-01-reconciliation-lifecycle-concurrency.md` that described the transaction-level advisory-lock query itself as occurring before snapshot establishment. The accounting invariant remains unchanged: the supported `evaluating`/`review_required` → `reconciled` transition must validate one current, database-owned source/review snapshot while excluding concurrent mutations of the same reconciliation aggregate.

## RED

Commit `9214c056149be43f03f2014638ff5c777d1f5b81` added `tests/test_reconciliation_lifecycle_snapshot_lock_order.py`. The contract requires a session-level lifecycle lock to be acquired and committed before the fresh repeatable-read authority transaction begins, then requires the existing transaction-level lifecycle lock before authority reads. The predecessor `0070cacb3a9804706a0009aff18add2b076a3f9b` has no `_lifecycle_authority_session` boundary and therefore does not satisfy that contract.

## Causal repair

Commit `dab17c4c7cf3d4922e7259f1a20fff6bdc30e7c6` introduced `_lifecycle_authority_session()` and changed only the reconciliation lifecycle transaction boundary:

1. acquire an exclusive session-level advisory lock using the same `(tenant_reference, reconciliation_run_lifecycle:<run_id>)` key as the existing transaction-level lock;
2. commit the lock-acquisition transaction while retaining the session lock;
3. start a fresh transaction and set `REPEATABLE READ`;
4. acquire the matching transaction-level advisory lock, then read tenant/run/review/source authority and perform the existing transition command, run-status update and outbox write;
5. commit or roll back the authority transaction before releasing the session-level lock.

The repair does not change monetary arithmetic, reconciliation eligibility, maker-checker evidence, idempotency identity, RLS, source-population hashing, the `reconciled` state edge, posting authority, period-close authority, or Billing/AIS ownership boundaries. Commit `4ae56148a7c7d4f9060d81027f950db21f572332` adds success, acquisition-failure, authority-rollback and cleanup edge coverage for the lease boundary.

## Why this ordering

A session-level advisory lock survives transaction commit and is released explicitly or when the database session ends. That lets admission complete before a new repeatable-read transaction establishes its snapshot. The ordinary transaction-level lock is retained inside the authority transaction so existing reconciliation evidence writers that use the same aggregate key continue to serialize against the lifecycle command without changing their contract.

Rejected alternatives:

- **Keep `pg_advisory_xact_lock` as the first repeatable-read query.** Rejected because the blocking query itself establishes the repeatable-read snapshot.
- **Move evidence reads to `READ COMMITTED`.** Rejected because separate statements could observe different source/review states and weaken the single accounting authority snapshot.
- **Remove the transaction-level aggregate lock after adding a session lock.** Rejected because existing evidence mutation paths already coordinate through the transaction-level key; retaining it preserves the established concurrency contract.
- **Use a caller-side mutex.** Rejected because correctness must hold across processes and database clients, not only one application instance.

## Traceability

| Requirement | Exact implementation | Falsifiable evidence |
| --- | --- | --- |
| Admission precedes snapshot | `src/accounting_information_platform/reconciliation_lifecycle.py::_lifecycle_authority_session` | `test_session_lock_precedes_fresh_repeatable_read_snapshot` |
| Failed admission writes no authority fact | same helper | `test_session_lock_acquisition_failure_rolls_back_without_unlock_attempt` |
| Authority failure rolls back before lease release | same helper | `test_authority_error_rolls_back_before_releasing_session_lock` |
| Cleanup anomaly does not masquerade as normal completion | same helper | `test_missing_owned_session_lock_fails_closed_after_successful_authority_work` |
| Original accounting failure remains primary during cleanup failure | same helper | `test_cleanup_failure_does_not_replace_original_authority_error` |
| Exact monetary/review authority unchanged | `reconcile_reconciliation_run`, `_database_owned_close_projection_evidence`, `_transition_snapshot_hash` | existing lifecycle PostgreSQL, replay, bridge, approval and aggregate-membership suites on the same exact head |

The hosted Accounting Foundation run for exact head `4ae56148a7c7d4f9060d81027f950db21f572332` was queued when this record was written. Local reasoning or predecessor GREEN is not promoted to hosted exact-head evidence; merge remains blocked until the current descendant has real PostgreSQL, 100% statement/branch, security and review evidence.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html
