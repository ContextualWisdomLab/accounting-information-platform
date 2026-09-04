# Direct reconciliation lifecycle session-lock authority

## Problem

The supported `reconcile_reconciliation_run()` path must acquire the tenant/run session advisory lock, commit that acquisition, and only then open a fresh PostgreSQL `REPEATABLE READ` authority transaction. Current lock ownership alone cannot prove this order. A direct caller can establish snapshot S0 first, wait for the same session lock while another backend commits eligibility-changing evidence, acquire session+xact locks afterward, and still read S0.

That is an accounting-control defect even when PostgreSQL replaces caller-selected hashes. The retained transition command must represent source/review populations observed after the lifecycle serialization point, not merely a server-computed predecessor snapshot.

## Constraints

- Keep `reconciliation_run` as the aggregate root.
- Preserve PostgreSQL-owned statement/book population identities, exact Decimal bridge validation, maker-checker exception authority, idempotency, immutable command evidence, and transactional outbox pairing.
- Do not use caller GUCs or request flags as lock-order evidence.
- Reject stale-snapshot raw DML before database-authority population queries execute.
- Keep migration 0027 forward-only and unreleased; do not rewrite protected migration history.
- Keep broad administrative/break-glass identities outside the ordinary runtime capability model.

## RED

`tests/test_reconciliation_lifecycle_prelock_snapshot_red.py` uses two real PostgreSQL backends without timing sleeps. The worker starts `REPEATABLE READ` and deliberately executes a query to pin snapshot S0 before requesting the lifecycle session lock. A second backend owns the lifecycle lock and commits a new open exception. After the writer commits, the worker acquires session+xact lock forms and attempts the raw transition. The accepted behavior is `reconciliation_lifecycle_fresh_transaction_required`, no transition command, and a non-reconciled run.

The predecessor migration can see the live session and transaction locks plus `REPEATABLE READ`, so it cannot distinguish this stale transaction from the supported two-phase application path. That makes the regression a direct test of acquisition ordering, not lock possession.

## Selected repair

Migration `0027_reconciliation_lifecycle_session_lock_authority.sql` now makes PostgreSQL own acquisition-order evidence.

`accounting_core.acquire_reconciliation_lifecycle_session(tenant_reference, run_id)` validates the aggregate scope, acquires the exact tenant/run session advisory lock, then records an ephemeral `reconciliation_lifecycle_session_lease` for the current backend session. The lease contains the backend PID plus `pg_stat_activity.backend_start`, tenant/run identity, `pg_current_xact_id()` for the acquisition transaction, and database acquisition time. It is recorded only after lock grant. The caller commits that transaction while the session lock remains held.

The authority transaction then starts fresh at `REPEATABLE READ` and obtains the same tenant/run transaction advisory lock. Before any database-authority population trigger executes, `accounting_reconciliation_transition_000_session_lock_guard` proves:

- the backend holds a real session-level advisory lock by probing it with `pg_advisory_unlock`;
- the exact transaction-level advisory hold survives that probe in `pg_locks`;
- the current transaction is `REPEATABLE READ`;
- an acquisition lease exists for this exact backend session, tenant and run;
- the lease acquisition transaction is not the current authority transaction; and
- the acquisition timestamp is not later than the authority transaction start.

The guard immediately restores the session hold after the lock-type probe. A raw caller that merely acquires both lock forms after snapshot S0 has no lease and fails. A caller that invokes the acquisition function inside S0 has a lease with the same transaction ID and also fails. The supported application calls the database acquisition function, commits, starts fresh `REPEATABLE READ`, obtains the xact lock, performs authority work, then calls the database release function in `finally`.

`accounting_core.release_reconciliation_lifecycle_session` deletes the current backend lease before explicitly releasing the session lock. If a backend disappears before release, PostgreSQL releases the session lock at disconnect. Backend-start identity prevents PID reuse from inheriting stale coordination evidence, and later acquisitions remove disconnected-backend leases.

## Boundary

The lease is coordination evidence, not accounting truth. It contains no journal balance, statement amount, reconciliation decision, customer PII, or posting authority. It does not change the book-to-bank equation or make bank statement evidence capable of posting journals. Billing and other foreign systems remain outside this database boundary and cannot use the lease as cross-service authority.

The migration keeps a database-side bypass invariant while issue #44 remains open. That follow-up must expose only the named lifecycle command to the least-privilege runtime role and must not grant raw transition/status/outbox DML. A superuser or equivalent break-glass identity can always subvert database controls and therefore remains separately governed and audited rather than represented as product runtime authority.

## Verification

The focused repository contracts require the lease table, database acquisition/release functions, acquisition transaction ID, transaction-start comparison, current lock proof, trigger ordering, and installer inclusion. The supported-path unit regression requires database-owned acquire → commit → fresh `REPEATABLE READ` → transaction lock and database-owned release in `finally`.

Positive raw PostgreSQL database-authority tests use the same acquisition function and commit boundary before entering their fresh authority transaction. Existing tests continue to prove caller identity replacement, timezone-independent hashes, exact bridge rejection, direct READ COMMITTED rejection, and transaction-lock-only rejection.

No source inspection is GREEN evidence. The current successor head must execute the real PostgreSQL RED/GREEN boundary and then complete the exact 100% owned production statement/branch and meaningful edge-case coverage, public-docstring/repository contracts, SAST/security/dependency checks, reproducible package/SBOM/provenance and current-head review gates before integration.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking—Advisory locks*. https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: The `pg_locks` view*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: The cumulative statistics system*. https://www.postgresql.org/docs/18/monitoring-stats.html
