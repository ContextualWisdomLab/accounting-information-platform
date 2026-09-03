# Direct reconciliation lifecycle session-lock authority

## Problem

The supported `reconcile_reconciliation_run()` path acquires the tenant/run session advisory lock, commits that acquisition, and only then opens a fresh PostgreSQL `REPEATABLE READ` authority transaction. The direct table path previously entered `accounting_reconciliation_transition_database_authority_guard` before the later transition-hash trigger acquired the transaction advisory lock. A raw transition statement could therefore derive database-owned statement/book/review authority from a snapshot established before a competing lifecycle writer committed, wait later in the same statement/transaction, and then continue without recreating the authority snapshot.

That is an accounting-control defect even though caller-selected hashes are replaced by PostgreSQL. The retained transition command must represent a source/review population observed after the lifecycle serialization point, not merely a server-computed predecessor snapshot.

## Constraints

- Keep `reconciliation_run` as the aggregate root and preserve the application session-lock commit followed by fresh `REPEATABLE READ` protocol.
- Do not weaken PostgreSQL-owned statement/book population identities, exact Decimal bridge validation, maker-checker exception authority, idempotency, immutable command evidence, or transactional outbox pairing.
- Do not rely on a caller-supplied GUC or Boolean flag as lock evidence.
- Raw table DML that does not enter the required lock protocol must fail closed before any database-authority population query executes.
- The repair is an unreleased forward migration; protected/released migration history is not rewritten.

## RED

`tests/test_reconciliation_lifecycle_direct_session_lock_postgres.py` uses real PostgreSQL. One connection owns the tenant/run lifecycle transaction lock and creates an eligibility-changing open exception while a second connection attempts raw transition DML without the required session lock under both `READ COMMITTED` and `REPEATABLE READ`. The test observes PostgreSQL blocking edges when the predecessor path reaches them and requires the raw path to fail with `reconciliation_lifecycle_session_lock_required`, never to resume into authority from the predecessor snapshot.

A separate RED deliberately acquires only the exact transaction advisory lock in `REPEATABLE READ`. That lock must not be accepted as evidence that the caller owns the session-scoped lifecycle lock. The test was committed before the corresponding lock-type repair. Hosted execution is exact-head evidence only when the workflow actually runs; queued or predecessor results remain non-passing.

## Selected repair

Migration `0027_reconciliation_lifecycle_session_lock_authority.sql` installs a first-sorting `BEFORE INSERT` trigger on `accounting_core.reconciliation_run_transition_command`, before `accounting_reconciliation_transition_database_authority_guard` can query reconciliation populations.

PostgreSQL session-level and transaction-level advisory locks share the same key space, and `pg_locks` does not by itself identify the acquisition API. The guard therefore verifies the two required lock forms together rather than treating one `pg_locks` row as sufficient evidence:

1. `pg_advisory_unlock(hashtext(tenant_reference), hashtext(lifecycle_scope))` must report that the backend actually owned a session-level hold. Transaction-only ownership returns false and is rejected.
2. While that session hold is temporarily decremented, `pg_locks` must still report the exact two-int tenant/run key as a granted `ExclusiveLock` for `pg_backend_pid()`. That surviving hold is the required transaction-level lock, so no other backend can enter the key during the probe.
3. The trigger immediately reacquires the session lock before returning or rejecting, restoring the application lock lifetime.
4. `current_setting('transaction_isolation')` must be `repeatable read`.

The application already follows the stronger sequence: acquire session lock, commit, open fresh `REPEATABLE READ`, then acquire the transaction lock before authority reads. Raw database acceptance that intentionally exercises the table-level authority must follow the same sequence. A raw insert with neither lock or with only the transaction lock fails before database-owned snapshot derivation.

The guard does not trust caller snapshot/population hashes; the existing database authority overlay still replaces them and verifies exact bridge arithmetic. It also does not create posting, reversal, period-close, or accounting-policy authority.

## Alternatives rejected

**Acquire only `pg_advisory_xact_lock` inside the existing trigger chain.** Rejected because the statement or repeatable-read transaction snapshot can already exist before that later wait completes.

**Treat an exact `pg_locks` row as session-lock proof.** Rejected because session and transaction advisory locks share the same key space and the view does not encode which acquisition API created the hold.

**Switch lifecycle authority to `READ COMMITTED`.** Rejected because sequential review, exception, bank-statement, journal, and bridge queries could observe different statement snapshots.

**Trust a session GUC or request flag.** Rejected because a direct SQL caller could forge it without owning the lock manager state.

**Remove direct database authority and trust only the Python application path.** Rejected for this slice because the current database model intentionally treats PostgreSQL constraints and triggers as an independent bypass boundary. Replacing that architecture requires an explicit capability/ADR change.

## Risk and follow-up

The database guard verifies the live lock state and isolation mode; the application and positive raw acceptance tests remain responsible for the complete acquisition order, including the preliminary session-lock commit before the fresh repeatable-read transaction. A database principal with broad advisory-lock and raw table authority could deliberately reproduce both lock forms in another order, which is one reason raw lifecycle-table DML must not be granted to the eventual least-privilege runtime role. Issue #44 should expose only the named lifecycle command boundary and remove raw status/transition/outbox authority from application identities.

Exact PostgreSQL acceptance remains mandatory. The unchanged candidate must exercise both isolation modes, transaction-lock-only rejection, exact tenant/run key matching, positive supported finalization, safe raw database-authority acceptance, forged identity replacement, untied bridge rejection, 100% owned statement/branch coverage, public-docstring/repository contracts, SAST/security/dependency checks, reproducible package/SBOM/provenance, and current-head review before this finding can be closed.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking—Advisory locks*. https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: The `pg_locks` view*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
