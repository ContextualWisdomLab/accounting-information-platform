# Direct reconciliation lifecycle session-lock authority

## Problem

The supported `reconcile_reconciliation_run()` path acquires the tenant/run session advisory lock, commits that acquisition, and only then opens a fresh PostgreSQL `REPEATABLE READ` authority transaction. Current lock ownership cannot prove this order by itself. A direct caller can establish snapshot S0 first, wait for the same session lock while another backend commits eligibility-changing evidence, acquire session+xact locks afterward, and still read S0.

A committed lease closes only part of that gap. The backend can acquire the canonical lease, commit it, release the session hold directly while leaving the lease row, establish S0, allow another backend to commit a new exception, then reacquire both lock forms directly. The old lease is still snapshot-visible unless the database treats the intervening evidence mutation as a lease invalidation event.

That is an accounting-control defect even when PostgreSQL replaces caller-selected hashes. A retained transition command must represent source/review populations admitted after the lifecycle serialization boundary, not a server-computed predecessor snapshot.

## Constraints

- Keep `reconciliation_run` as the aggregate root.
- Preserve PostgreSQL-owned statement/book population identities, exact Decimal bridge validation, maker-checker exception authority, idempotency, immutable command evidence, and transactional outbox pairing.
- Do not use caller GUCs, request flags, or caller timestamps as acquisition-order evidence.
- Reject stale-snapshot raw DML before database-authority population queries execute.
- Keep migration 0029 forward-only and unreleased; do not rewrite protected migration history.
- Keep broad administrative/break-glass identities outside the ordinary runtime capability model.

## RED

`tests/test_reconciliation_lifecycle_stacked_session_admission_postgres.py` reproduces the current continuity defect with real PostgreSQL backends. The owner acquires and commits the canonical lifecycle lease, releases the session hold directly without deleting the lease, then starts `REPEATABLE READ` and pins snapshot S0. A second backend commits a new open exception while the lifecycle key is available. The owner still sees the predecessor exception population, reacquires both session and transaction advisory locks directly, and attempts the raw transition.

The predecessor admission logic can prove that session and transaction lock forms are currently held and that the old lease predates the authority transaction, but it cannot prove continuous session-lock ownership across the interval in which the exception committed. The repaired behavior is SQLSTATE `40001` with `reconciliation_lifecycle_fresh_transaction_required`; the caller must reacquire canonically and start a new authority transaction.

## Selected repair

Migration `0029_reconciliation_lifecycle_session_lock_authority.sql` owns acquisition and continuity evidence.

`accounting_core.acquire_reconciliation_lifecycle_session(tenant_reference, run_id)` validates aggregate scope and backend identity, takes the transaction advisory key while normalizing same-backend session holds, restores exactly one session hold, and records `reconciliation_lifecycle_session_lease` when there is no continuously held prior lease. The lease stores backend PID plus `pg_stat_activity.backend_start`, tenant/run identity, acquisition transaction ID, and database acquisition time. The supported caller commits that transaction while the normalized session hold remains active.

The existing migration-0019 evidence guards already serialize candidate, match, statement allocation, journal allocation, approval, and exception mutations on the same tenant/run transaction advisory key. Migration 0029 adds `AFTER` invalidation triggers to those six authority-relevant tables. A successful mutation deletes prior lifecycle session leases for the same tenant/run in the same commit. A rejected or rolled-back mutation does not change lease state.

The authority transaction starts fresh at `REPEATABLE READ` and obtains the same transaction advisory lock. Before database snapshot derivation, `accounting_reconciliation_transition_000_session_lock_guard` proves the session hold, drains all stacked session holds, verifies that the transaction lock remains, restores exactly one session hold, and then locks the exact lease row with `SELECT ... FOR UPDATE`.

That row lock is the continuity check. If an eligibility-changing transaction invalidated the lease after S0 was established, PostgreSQL cannot lock the now-changed snapshot-visible tuple under `REPEATABLE READ`; it raises serialization failure. The guard preserves SQLSTATE `40001` and reports `reconciliation_lifecycle_fresh_transaction_required`. If invalidation committed before S0, the lease is simply absent and fails the same freshness boundary. Releasing and reacquiring the lock without any intervening authority mutation is not treated as a financial-state change.

`accounting_core.release_reconciliation_lifecycle_session` deletes the current backend lease and drains every matching session-level hold. Backend-start identity prevents PID reuse from inheriting stale coordination evidence, and later acquisitions clean leases whose backend session no longer exists.

Migration 0029 revokes `PUBLIC EXECUTE` on both `SECURITY DEFINER` lifecycle helpers in their creation transaction. Migration `0030_reconciliation_lifecycle_capability_privileges.sql` repeats those revocations as forward repair. Issue #44 remains the owner for the purpose-limited runtime capability and must not grant raw transition/status/outbox DML.

## Boundary

The lease is ephemeral coordination evidence, not accounting truth. It contains no journal balance, statement amount, reconciliation decision, customer PII, posting authority, or Billing-owned fact. FORCE RLS remains tenant-bound. The invalidation trigger derives tenant/run identity from the mutated reconciliation row rather than caller-supplied scope.

A superuser or equivalent migration owner can subvert database controls by design and remains outside the product runtime threat boundary. That is not a reason to weaken ordinary runtime controls or to represent owner-level raw SQL as a supported product capability.

## Verification

The stale-lease continuity regression must fail with SQLSTATE `40001` and `reconciliation_lifecycle_fresh_transaction_required` after the late exception commits. The existing stacked-session regression must continue proving that multiple raw session holds cannot masquerade as a transaction lock. `tests/test_reconciliation_lifecycle_session_lock_reentrancy_postgres.py` must continue proving canonical repeated acquisition leaves one hold and one release frees the key. Positive database-authority tests must use canonical acquire → commit → fresh `REPEATABLE READ` → xact lock.

No source inspection is GREEN evidence. The successor exact head must execute the real PostgreSQL regression and then complete exact 100% owned production statement/branch and edge-case coverage, repository contracts, SAST/security/dependency checks, reproducible package/SBOM/provenance, and current-head review before integration.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking—Advisory locks*. https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: The `pg_locks` view*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
