# ADR 0066: Direct reconciliation lifecycle authority requires a committed, continuous session-lock lease

- Status: Proposed
- Date: 2026-09-03
- Bounded context: Bank Reconciliation / Evidence and Audit
- Depends on: ADR 0060 reconciliation-run lifecycle authority
- Follow-up: issue #44 for the least-privilege database capability

## Problem

The supported reconciliation lifecycle command acquires a tenant/run session advisory lock, commits that acquisition, starts a fresh PostgreSQL `REPEATABLE READ` transaction, and then acquires the matching transaction advisory lock. A direct table caller can hold the same two advisory-lock forms and still violate that ordering: it can establish a repeatable-read snapshot, wait for the session lock while another backend commits eligibility-changing evidence, acquire the session and transaction locks, then insert a transition from the predecessor snapshot.

Live lock state alone does not encode acquisition history. A persisted lease improves that boundary, but the lease also cannot stand alone. A backend can acquire the canonical lease, commit it, release the session lock directly while leaving the lease row, establish snapshot S0, let another backend commit a new exception, and then reacquire both advisory-lock forms directly. If the transition guard accepts the old lease row without proving that no eligibility-changing commit invalidated it, the stale snapshot can be mistaken for current reconciliation authority.

PostgreSQL session advisory locks are also reentrant. A repeated same-backend acquisition can stack multiple holds on one tenant/run key while a single lease row remains. The admission guard must distinguish remaining session holds from the required transaction lock, and normal release must not leave an invisible residual blocker.

The coordination helpers are `SECURITY DEFINER`. PostgreSQL grants `EXECUTE` on newly created functions to `PUBLIC` by default, so a clean install must revoke that default before ordinary schema users can invoke the lock capability.

## Constraints

The repair must preserve database-owned statement/book populations, exact Decimal book-to-bank arithmetic, immutable reviewed evidence, maker-checker exception authority, transition idempotency, command/status/outbox atomicity, tenant RLS, and the separation between reconciliation evidence and General Ledger/period-close authority. It must not use a caller GUC or caller-supplied timestamp as proof of lock ordering.

The application and any intentional direct-database acceptance path use one canonical ordering boundary. Broad break-glass database ownership remains outside the product runtime contract. Generic runtime/read identities must not inherit lifecycle lock execution through PostgreSQL defaults. Retry or nested acquisition must not increase the session-lock hold count.

## Decision

Migration `0029_reconciliation_lifecycle_session_lock_authority.sql` owns session-lock acquisition and continuity evidence in PostgreSQL.

`accounting_core.acquire_reconciliation_lifecycle_session(tenant_reference, run_id)` validates tenant/run scope and the current backend identity. It first takes the matching transaction advisory lock, drains any session-level holds for the exact key, and restores exactly one session hold. If the same backend still held a previously leased session lock, the committed lease is preserved so a retry cannot move the freshness boundary forward. If the lease exists but the session hold has been lost, or no lease exists, the helper writes a new lease in the current acquisition transaction. The lease records backend PID plus `backend_start`, tenant/run identity, acquisition transaction ID, and database acquisition time. The supported caller commits that transaction while the single normalized session hold remains active.

A later authority transaction must be `REPEATABLE READ` and hold the matching transaction advisory lock. `accounting_reconciliation_transition_000_session_lock_guard` drains every session hold for the key before consulting `pg_locks`; only an advisory row that remains can prove the transaction-level hold. Exactly one session hold is then restored. This removes the ambiguity where a second stacked session hold could masquerade as the xact lock.

Lease continuity is invalidated by successful reconciliation-eligibility mutations. The existing migration-0019 lifecycle guards already serialize `reconciliation_candidate`, `reconciliation_match`, `statement_match_allocation`, `journal_match_allocation`, `reconciliation_approval`, and `reconciliation_exception` mutations on the same tenant/run transaction advisory lock. Migration 0029 adds `AFTER` invalidation triggers for those tables. A successful mutation deletes all older session-lease rows for that tenant/run in the same commit. Failed or rolled-back mutations do not invalidate a lease.

The transition guard locks its exact committed lease row with `SELECT ... FOR UPDATE`; it does not accept a merely snapshot-visible tuple. If another transaction invalidated that lease after the current `REPEATABLE READ` snapshot began, PostgreSQL raises SQLSTATE `40001` when the stale transaction attempts to lock the changed tuple. The guard re-raises that condition as `reconciliation_lifecycle_fresh_transaction_required`. The caller must reacquire the canonical session lease, commit it, and retry from a fresh authority transaction. If the invalidation committed before the authority snapshot began, no matching lease is visible and the same freshness boundary fails normally.

This means direct release/reacquisition is not itself treated as an accounting defect when no authority-relevant fact changed. It becomes non-authoritative as soon as a serialized eligibility mutation commits during the lost-lock interval. The database therefore does not need an unavailable advisory-lock acquisition timestamp; mutation invalidation plus PostgreSQL's repeatable-read row-lock conflict provides the causal proof.

`accounting_core.release_reconciliation_lifecycle_session(...)` deletes the current backend lease and drains every matching session-level hold. Backend identity includes `pg_stat_activity.backend_start`, so PID reuse cannot inherit a disconnected backend's lease. A later acquisition also removes leases whose backend session no longer exists.

Both lifecycle session helpers are `SECURITY DEFINER`. Migration 0029 revokes `PUBLIC EXECUTE` in the same transaction that creates them. Migration `0030_reconciliation_lifecycle_capability_privileges.sql` repeats the revocation as forward repair for an already-applied predecessor. Issue #44 remains the owner for a purpose-limited `NOLOGIN` runtime capability; it must not grant raw transition/status/outbox DML or collapse database capability into tenant or Keyverse/application authorization.

The supported sequence is:

`normalized session lock + committed lease -> fresh REPEATABLE READ -> matching transaction lock -> database authority derivation -> transition/status/outbox transaction -> release lease + session lock`.

Any successful candidate/match/allocation/approval/exception mutation between lease acquisition and transition invalidates the earlier lease. A retry begins again from the left side of that sequence.

## Alternatives

**Keep live lock-state proof only.** Rejected because current session+xact ownership does not encode when a repeatable-read snapshot was established.

**Treat a persisted lease row as sufficient.** Rejected because a lease can outlive a raw session-lock release and later reacquisition.

**Use `pg_locks.waitstart` or a lock-manager timestamp.** Rejected because `waitstart` describes a current wait and does not retain the historical grant boundary required by the authority trigger.

**Inspect only `pg_locks`.** Rejected because session and transaction advisory locks use the same key space and `pg_locks` does not identify the acquisition API or continuity history.

**Use a caller GUC or caller timestamp.** Rejected because direct SQL can forge either independently of PostgreSQL-owned authority evidence.

**Switch lifecycle authority to `READ COMMITTED`.** Rejected because sequential statement, journal, allocation, approval, exception, and bridge queries could observe different statement snapshots.

**Use a transaction-controlling stored procedure as the only boundary.** Not selected because PostgreSQL transaction-control invocation restrictions do not fit the current authenticated application boundary. The Python application can commit acquisition explicitly while PostgreSQL owns the lease and admission evidence.

**Revoke advisory-lock built-ins from ordinary database users.** Rejected as an overly broad cluster-level policy and not a substitute for a bounded AIS lifecycle capability.

**Remove all database-side transition authority.** Deferred to issue #44. PostgreSQL remains an independent invariant boundary in this slice; ordinary runtime identities must ultimately receive only the named command capability rather than raw authority-table DML.

## Risk and effect

Lease invalidation deliberately favors correctness over availability. If a serialized eligibility mutation commits after a caller established its authority snapshot, the caller can receive SQLSTATE `40001` and must retry from a new acquisition transaction. This is the same fail-closed class as other repeatable-read serialization conflicts and prevents a stale accounting decision from being promoted to reconciled authority.

The invalidation table is coordination evidence, not financial truth. It carries no balances, journal decisions, customer identity, or billing facts. Its FORCE RLS policy remains bound to the authenticated tenant, and the invalidation trigger derives tenant/run scope from the mutated row rather than caller input.

A superuser or equivalent migration owner can still bypass database controls by design. That principal is outside the product runtime threat boundary and remains separately governed and audited. Issue #44 must continue reducing runtime privilege rather than treating owner-level SQL as a normal product capability.

## Verification

`tests/test_reconciliation_lifecycle_stacked_session_admission_postgres.py` contains two real PostgreSQL attacks. The first proves stacked session holds cannot satisfy transaction-lock proof. The second acquires and commits a canonical lease, releases the session hold directly, establishes a `REPEATABLE READ` snapshot, commits a new exception from another backend, then reacquires session and transaction locks without the canonical helper. The transition must fail with SQLSTATE `40001` and `reconciliation_lifecycle_fresh_transaction_required`; the stale snapshot must not authorize reconciliation.

`tests/test_reconciliation_lifecycle_session_lock_reentrancy_postgres.py` proves repeated canonical acquisition leaves one hold and one canonical release frees the exact tenant/run key. `tests/test_reconciliation_lifecycle_session_lease_rls_postgres.py` proves ENABLE/FORCE RLS and tenant isolation for lease state. Positive direct-database tests must acquire through the canonical helper, commit, start fresh `REPEATABLE READ`, and then acquire the xact lock.

The complete candidate still requires one unchanged exact head to pass real PostgreSQL behavior, exact 100% owned production statement/branch and edge-case coverage, public docstrings, repository contracts, SAST/security/dependency review, reproducible package/SBOM/provenance, current-head review, migration/recovery evidence, and live ruleset admission before integration.

## References

National Institute of Standards and Technology. (2020, updated 2025). *Security and privacy controls for information systems and organizations (NIST SP 800-53 Rev. 5), AC-6 Least Privilege*. U.S. Department of Commerce. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking—Advisory locks*. https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: The pg_locks view*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html

PostgreSQL Global Development Group. (2026g). *PostgreSQL 18 documentation: Privileges*. https://www.postgresql.org/docs/18/ddl-priv.html
