# ADR 0066: Direct reconciliation lifecycle authority requires a committed session-lock lease

- Status: Proposed
- Date: 2026-09-03
- Bounded context: Bank Reconciliation / Evidence and Audit
- Depends on: ADR 0060 reconciliation-run lifecycle authority
- Follow-up: issue #44 for the least-privilege database capability

## Problem

The supported reconciliation lifecycle command acquires a tenant/run session advisory lock, commits that acquisition, starts a fresh PostgreSQL `REPEATABLE READ` transaction, and then acquires the matching transaction advisory lock. A direct table caller can hold the same two advisory-lock forms and still violate that ordering: it can establish a repeatable-read snapshot, wait for the session lock while another backend commits eligibility-changing evidence, acquire the session and transaction locks, then insert a transition from the predecessor snapshot.

Migration 0027 originally checked only live session-lock ownership, matching transaction-lock ownership, and `REPEATABLE READ`. Those facts are necessary but do not prove that the authority transaction began after session-lock grant. PostgreSQL cannot refresh an already established repeatable-read snapshot inside the transition trigger.

The same repair creates two `SECURITY DEFINER` coordination functions. PostgreSQL grants `EXECUTE` on newly created functions to `PUBLIC` by default. Without an explicit revoke, a runtime or reporting identity that merely has schema `USAGE` could invoke the tenant/run lock helper even though issue #44 reserves lifecycle execution for a later purpose-limited capability. For the acquire helper, unintended invocation is also an availability risk because the caller can hold the serialization key without reconciliation business authority.

Session advisory locks are reentrant and stack by acquisition count. A repeated call to the acquire helper on the same backend can therefore create multiple holds on one tenant/run key while the lease table still contains only one row. If release removes that row and unlocks only once, the backend retains an invisible residual hold until disconnect and blocks later reconcilers even though the owner path reports successful release.

## Constraints

The fix must preserve database-owned statement/book populations, exact Decimal book-to-bank arithmetic, immutable reviewed evidence, maker-checker exception authority, transition idempotency, outbox pairing, and the separation between reconciliation evidence and General Ledger/period-close authority. Direct foreign database access remains prohibited; this ADR governs only AIS's own database bypass boundary.

The application and any intentional direct-database acceptance path must use one canonical ordering boundary. A caller flag or GUC is not sufficient. Broad break-glass database authority is not a product runtime contract and must not be represented as ordinary application capability. Generic runtime/read roles must not inherit lifecycle lock execution through PostgreSQL defaults. Retry/nested acquisition must not increase the session-lock hold count.

## Decision

Migration `0027_reconciliation_lifecycle_session_lock_authority.sql` owns session-lock acquisition evidence in PostgreSQL rather than inferring historical ordering from current lock state.

`accounting_core.acquire_reconciliation_lifecycle_session(tenant_reference, run_id)` validates the tenant/run and current backend identity. It takes the matching transaction advisory lock before normalizing any existing session hold, so no other backend can enter the tenant/run key during normalization. The helper removes all current-backend session holds for the exact key and reacquires exactly one session hold. If the same backend still held a previously leased session lock, the existing committed lease is preserved; a retry therefore does not move the freshness boundary forward. If the lease exists but the session hold is absent, or no lease exists, the helper records a new `reconciliation_lifecycle_session_lease` for the current transaction. The lease stores `backend_pid`, `backend_start`, tenant/run identity, the acquisition transaction ID, and database acquisition time. The supported caller commits that acquisition transaction while the single normalized session lock remains held.

This distinction closes the stale-lease case: a backend cannot release its lock, establish a stale transaction snapshot, reacquire through the helper, and then rely on an old lease timestamp. Reacquisition without the live session hold writes the current transaction ID, so a transition in that same transaction fails the distinct-transaction freshness guard. Repeated acquisition while both the live lock and valid lease remain present only normalizes the session hold to one and preserves the older committed lease.

The caller then opens a fresh `REPEATABLE READ` transaction and acquires the matching transaction advisory lock. The first-sorting `accounting_reconciliation_transition_000_session_lock_guard` still proves the exact session and transaction lock forms and isolation level before the database-authority trigger can read reconciliation populations. It additionally requires a lease for the same backend session, tenant and run whose acquisition transaction differs from the current authority transaction and whose acquisition time is not later than the current transaction start. A missing or same-transaction lease fails with `reconciliation_lifecycle_fresh_transaction_required`.

The backend identity uses both PID and `pg_stat_activity.backend_start`; PID reuse therefore cannot inherit a disconnected backend's lease. The acquisition function removes leases whose backend session no longer exists. `accounting_core.release_reconciliation_lifecycle_session(...)` deletes the current lease before releasing the one normalized session lock. The lease table is not accounting truth and carries no balances, decisions, identities of customers, or journal facts.

Both lifecycle session helpers are `SECURITY DEFINER`, so migration 0027 revokes `PUBLIC EXECUTE` on each helper in the same transaction that creates it. This removes the clean-install privilege window recommended against by PostgreSQL's privilege guidance. Migration `0028_reconciliation_lifecycle_capability_privileges.sql` repeats the revocation as a forward repair for a database that may already have applied a predecessor 0027. The canonical installer requires 0028. No generic runtime/read identity receives this capability implicitly.

Issue #44 remains the owner for the eventual database capability. That follow-up must explicitly grant only the canonical lifecycle execution surface to a purpose-limited `NOLOGIN` capability role after application authorization is stable. It must not grant raw INSERT on `reconciliation_run_transition_command`, generic UPDATE on `reconciliation_run`, or direct INSERT of reconciliation authority outbox events. Database capability membership remains separate from tenant binding and Keyverse/application authorization. Capability acceptance must also prove repeated exact acquisition remains one-hold/one-release.

The supported application sequence is therefore:

`database-owned normalized session lock + committed lease -> fresh REPEATABLE READ -> matching transaction lock -> authority derivation -> transition/status/outbox transaction -> release lease + session lock`.

A raw caller that merely invokes `pg_advisory_lock` after establishing snapshot S0 has no database-owned acquisition lease and fails before statement, journal, allocation, approval, exception, or bridge authority is admitted. Invoking the acquisition function inside the same already-stale transaction also fails because a new acquisition lease carries the current transaction ID.

## Alternatives

**Keep live lock-state proof only.** Rejected because current session+xact ownership does not encode when a repeatable-read snapshot was established.

**Use `pg_locks.waitstart` or lock-manager timestamps.** Rejected because `waitstart` describes current waiting and is null once the lock is granted; it does not retain the historical grant boundary needed by the authority trigger.

**Inspect only `pg_locks`.** Rejected because session and transaction advisory locks share the same key space and the view does not identify the acquisition API or historical order.

**Use a caller GUC to attest safe ordering.** Rejected because direct SQL can forge it independently of PostgreSQL-owned evidence.

**Switch lifecycle authority to `READ COMMITTED`.** Rejected because sequential review, exception, statement, journal and bridge queries could observe different statement snapshots.

**Rely on a transaction-controlling stored procedure alone.** Not selected because PostgreSQL transaction control has invocation and `SECURITY DEFINER` restrictions that do not fit the current authenticated application boundary. The existing Python application can commit the acquisition transaction explicitly while PostgreSQL owns the lease evidence.

**Treat an existing lease row as sufficient for duplicate acquisition.** Rejected because the row can outlive a released session lock; returning solely from lease state would let a stale transaction reacquire a live lock while preserving an old freshness boundary.

**Unstack session holds without a transaction lock.** Rejected because targeted unlock/relock would briefly expose the tenant/run key to another backend. The helper first owns the matching transaction advisory lock for the normalization transaction.

**Leave default function privileges unchanged.** Rejected because schema access would silently imply invocation authority for a security-definer coordination primitive. Restricting only the later capability role does not remove PostgreSQL's initial `PUBLIC EXECUTE` grant.

**Revoke only in migration 0028.** Rejected for clean installs because the separately committed 0027 would leave an interval in which the function is callable by any principal with schema access. The creation transaction performs the revoke; 0028 exists only to repair already-applied predecessor 0027 installations.

**Remove all database-side transition authority.** Deferred to the capability redesign in issue #44. PostgreSQL remains an independent invariant boundary in this slice, but ordinary runtime identities must eventually receive only the named command capability rather than raw transition/status/outbox DML.

## Risk and effect

The lease proves that the transition transaction is different from the transaction in which the current backend acquired and recorded the session lock. The trigger also verifies the live lock state, so deleting or fabricating a lease without the matching session/xact locks does not admit authority. A superuser or equally broad break-glass identity can still subvert database controls by design; that identity is outside the product runtime threat boundary and must remain separately governed and audited.

The lease is ephemeral coordination evidence. If a backend disconnects before normal release, PostgreSQL releases its session advisory lock automatically; stale lease rows are ignored by backend-start identity and removed by a later acquisition. This does not rewrite reconciliation facts or make lease state part of financial reporting.

Repeated authorized acquisition is idempotent with respect to session-lock count. Normalization uses the transaction-level key to prevent another backend from entering while the owner collapses any stacked session holds to one. One explicit release then removes the lease and releases that one hold; connection teardown remains a recovery backstop, not the normal lifecycle.

Revoking `PUBLIC EXECUTE` deliberately means the ordinary runtime cannot call the lifecycle helpers until the deployment owner grants the future issue-#44 capability. This is fail-closed behavior, not a temporary broad grant. An installation upgraded from an earlier 0027 receives the same restriction through 0028.

## Verification

The real PostgreSQL RED `tests/test_reconciliation_lifecycle_prelock_snapshot_red.py` pins snapshot S0 before requesting the lifecycle session lock, lets another backend commit a new exception while holding the serialization boundary, then acquires the two lock forms and attempts raw transition authority. The repaired path must fail with `reconciliation_lifecycle_fresh_transaction_required`, leave the run non-reconciled, and persist neither transition command nor matching authority event.

`tests/test_reconciliation_lifecycle_session_lock_reentrancy_postgres.py` acquires the canonical helper twice on one backend, releases once, then requires a second backend to acquire the exact tenant/run advisory key. A residual stacked hold is a failure. This regression is real PostgreSQL evidence rather than a mock of lock calls.

Positive direct-database tests must use `acquire_reconciliation_lifecycle_session`, commit, open fresh `REPEATABLE READ`, then acquire the transaction lock before inserting. Repository contracts require the acquisition lease, backend-session identity, distinct transaction IDs, trigger ordering, and canonical installer inclusion. The supported application path must exercise the same database-owned acquisition/release functions.

`tests/test_reconciliation_lifecycle_session_lock_authority_contract.py` requires creation-time and forward-upgrade privilege revocation and requires the canonical installer to include migration 0028. `tests/test_postgres_runtime_rls.py` provisions a real tenant-bound login that is non-owner, non-superuser, and non-`BYPASSRLS`, grants the ordinary runtime schema/table surface, and requires PostgreSQL `InsufficientPrivilege` for both lifecycle helpers. The later issue-#44 capability must add the inverse positive acceptance without weakening these generic-runtime denials.

The complete candidate still requires one unchanged exact head to pass real PostgreSQL behavior, exact 100% owned production statement/branch and edge-case coverage, public docstrings, repository contracts, SAST/security/dependency review, reproducible package/SBOM/provenance, current-head review, migration/recovery evidence and live ruleset admission before integration.

## References

National Institute of Standards and Technology. (2020, updated 2025). *Security and privacy controls for information systems and organizations (NIST SP 800-53 Rev. 5), AC-6 Least Privilege*. U.S. Department of Commerce. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking—Advisory locks*. https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: The pg_locks view*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: The cumulative statistics system*. https://www.postgresql.org/docs/18/monitoring-stats.html

PostgreSQL Global Development Group. (2026g). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html

PostgreSQL Global Development Group. (2026h). *PostgreSQL 18 documentation: Privileges*. https://www.postgresql.org/docs/18/ddl-priv.html

PostgreSQL Global Development Group. (2026i). *PostgreSQL 18 documentation: ALTER DEFAULT PRIVILEGES*. https://www.postgresql.org/docs/18/sql-alterdefaultprivileges.html
