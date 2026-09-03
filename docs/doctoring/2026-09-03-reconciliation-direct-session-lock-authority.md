# Direct reconciliation lifecycle session-lock authority

## Problem

The supported `reconcile_reconciliation_run()` path acquires the tenant/run session advisory lock, commits that acquisition, and only then opens a fresh PostgreSQL `REPEATABLE READ` authority transaction. The direct table path previously entered `accounting_reconciliation_transition_database_authority_guard` before the later transition-hash trigger acquired the transaction advisory lock. A raw transition statement could therefore derive database-owned statement/book/review authority from a snapshot established before a competing lifecycle writer committed, wait later in the same statement/transaction, and then continue without recreating the authority snapshot.

That is an accounting-control defect even though caller-selected hashes are replaced by PostgreSQL. The retained transition command must represent a source/review population observed after the lifecycle serialization point, not merely a server-computed predecessor snapshot.

## Constraints

- Keep `reconciliation_run` as the aggregate root and preserve the application two-phase session-lock plus fresh-`REPEATABLE READ` protocol.
- Do not weaken PostgreSQL-owned statement/book population identities, exact Decimal bridge validation, maker-checker exception authority, idempotency, immutable command evidence, or transactional outbox pairing.
- Do not rely on a caller-supplied GUC or Boolean flag as lock evidence.
- Raw table DML that does not enter the safe protocol must fail closed before any database-authority population query executes.
- The repair is an unreleased forward migration; previously released/protected migration history is not rewritten.

## RED

`tests/test_reconciliation_lifecycle_direct_session_lock_postgres.py` uses two real PostgreSQL connections. One connection owns the tenant/run lifecycle transaction lock and creates an eligibility-changing open exception. A second connection begins a raw transition statement without the required pre-statement session lock under both default `READ COMMITTED` and explicit `REPEATABLE READ`. If the predecessor implementation reaches its later transaction-lock wait, the test releases the writer only after PostgreSQL reports the blocking edge. The raw path must still fail with `reconciliation_lifecycle_session_lock_required`; it may not resume from the predecessor statement/transaction snapshot.

The test was committed before the production repair. Hosted execution remains exact-head evidence only when the corresponding workflow actually runs; queued or predecessor results are non-passing.

## Selected repair

Migration `0027_reconciliation_lifecycle_session_lock_authority.sql` installs a first-sorting `BEFORE INSERT` trigger on `accounting_core.reconciliation_run_transition_command`. Before `...database_authority_guard` can query any reconciliation source population, the new trigger resolves the database-owned tenant reference, derives the exact two-int advisory-lock keys used by the application, and checks `pg_catalog.pg_locks` for a granted exclusive advisory lock owned by `pg_backend_pid()` in the current database.

The two-int advisory key is matched through `classid`, `objid`, and `objsubid = 2`, using unsigned 32-bit normalization of PostgreSQL `hashtext()` results for the OID-backed `pg_locks` columns. Absence of the exact backend-held lock raises SQLSTATE `55000` with stable marker `reconciliation_lifecycle_session_lock_required` before database-owned snapshot derivation begins.

This does not grant authority merely because a caller supplies snapshot/population hashes; those values are still replaced by the existing database authority overlay. It also does not turn reconciliation into posting or period-close authority.

## Alternatives rejected

**Acquire only `pg_advisory_xact_lock` inside the existing trigger chain.** Rejected because the statement or repeatable-read transaction snapshot can already exist before that later wait completes.

**Switch lifecycle authority to `READ COMMITTED`.** Rejected because sequential review, exception, bank-statement, journal, and bridge queries could then observe different statement snapshots.

**Trust a session GUC or request flag saying the lock was acquired.** Rejected because a caller with direct SQL capability could forge that assertion without owning the server lock.

**Remove direct database authority and trust only the Python application path.** Rejected for this slice because the checked-in database model deliberately treats PostgreSQL constraints/triggers as an independent bypass boundary. Replacing that architecture requires a separate ADR and capability migration, not an implicit weakening.

## Risk and follow-up

`pg_locks` is a server lock-manager view, so exact PostgreSQL acceptance is mandatory. The migration must be tested on PostgreSQL 18 with both isolation levels, exact tenant/run key matching, positive supported application finalization, direct raw rejection, and the existing bridge/population tests. The complete unchanged head must then pass 100% owned statement/branch coverage, public-docstring/repository contracts, SAST/security/dependency checks, reproducible package/SBOM/provenance, and current-head review before this finding can be resolved.

The broader least-privilege database capability should eventually expose only named reconciliation commands rather than raw transition/status/outbox DML. This migration remains defense in depth until that capability is integrated.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking—Advisory locks*. https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: The `pg_locks` view*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
