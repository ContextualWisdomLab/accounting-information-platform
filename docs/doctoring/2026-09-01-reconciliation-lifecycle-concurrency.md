# Doctoring record: reconciliation lifecycle concurrency and status authority

**Date:** 2026-09-01  
**Updated:** 2026-09-02  
**Scope:** stacked reconciliation lifecycle candidate on `accounting-information-platform`

## Research question

What concurrency and transaction contract is required so a reconciliation run can move from `evaluating`/`review_required` to `reconciled` without blessing stale, partially reviewed, caller-shaped, pre-terminal, replay-inconsistent, or cross-aggregate-reassigned accounting evidence?

## Authoritative findings

PostgreSQL 18 documents materially different snapshot behavior for `READ COMMITTED` and `REPEATABLE READ`. Under `READ COMMITTED`, each command sees rows committed before that command began. Under `REPEATABLE READ`, the transaction snapshot is established by the first non-transaction-control statement and is then retained for later reads. A transaction advisory lock is acquired through a SQL statement such as `SELECT pg_advisory_xact_lock(...)`; therefore that lock statement can itself establish a `REPEATABLE READ` snapshot before it waits.

That distinction invalidates the earlier assumption that “set repeatable read, then acquire the advisory lock before the first data query” necessarily yields a post-lock snapshot. If finalization waits behind a reconciliation-evidence writer, a repeatable-read transaction can retain a snapshot from before the writer commits and, after lock grant, still see the prior exception/review state. The result is fail-closed rather than false-green, but it can reject a valid operator action that has already committed and contradicts the advertised serialization contract.

The lifecycle finalizer therefore uses explicit PostgreSQL `READ COMMITTED`, takes `reconciliation_run_lifecycle:<run_id>` before authority reads, and relies on the database/application guards that require every supported mutation of candidate, match, allocation, approval, exception, exception-resolution, and run lifecycle evidence to acquire the same transaction-level advisory lock. If the finalizer waits, the statement executed after lock grant gets a fresh snapshot that includes the preceding guarded writer's commit. While finalization holds the lock, a later guarded mutation cannot change the aggregate behind its authority reads and transition/outbox write.

`SELECT ... FOR UPDATE` is retained for the `reconciliation_run` row so conflicting status writers serialize on aggregate state. Transaction-level advisory locks remain the wider application-defined run boundary because the aggregate evidence spans multiple tables and they release automatically when the transaction ends.

### References (APA 7th)

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html

## Code-to-evidence traceability

| Requirement | Implementation boundary | Falsifiable evidence |
| --- | --- | --- |
| Post-wait authority visibility | `reconcile_reconciliation_run()` sets `READ COMMITTED`, acquires `reconciliation_run_lifecycle:<run_id>`, then reads tenant/run/source/review state | Focused isolation regression requires `READ COMMITTED`; real PostgreSQL concurrency acceptance must prove a waiting finalizer observes evidence committed by the preceding lifecycle-lock holder |
| Aggregate serialization | Candidate/match/allocation/approval/exception/exception-resolution/run-transition mutation paths acquire the same run lifecycle transaction advisory lock | Migration/repository contracts plus real PostgreSQL freeze, membership, resolution and lifecycle tests |
| Aggregate initial state | Migration lifecycle trigger rejects every new `reconciliation_run` whose initial `run_status_code` is not `evaluating` | Real PostgreSQL raw `INSERT ... run_status_code='reconciled'` must fail with `reconciliation_lifecycle_initial_state` before deferred provenance could make a forged terminal run durable |
| Legal state edge | `reconciliation_run` row lock + migration status guard permits only `evaluating`/`review_required` → `reconciled`; every other changed target is rejected until a separate named command evolves the state machine | Raw SQL into `reconciled` without transition evidence and raw SQL into `not_reconciled` without a named command must both fail |
| Aggregate membership | Existing candidate/match/allocation/approval/exception rows may not change tenant/run ownership before lifecycle-lock selection | Repository contract proves the tenant/run reassignment guard precedes lifecycle lock; real PostgreSQL cross-run UPDATE must fail with `reconciliation_lifecycle_scope_immutable` |
| Durable command identity | `reconciliation_run_transition_command` stores tenant/run/idempotency/snapshot/statement population/book population/actor/purpose/effective time and database-owned command hash | Exact replay returns the same receipt and source-population references; changed key evidence conflicts |
| Review completeness | Service and database transition trigger reject proposed matches and approved/rejected matches without decision-consistent approval snapshot | Unit cases and migration trigger contract |
| Exception completeness | Service and DB trigger reject open exceptions and terminal exceptions lacking matching immutable maker-checker command evidence | Unit + PostgreSQL exception-resolution/finalization acceptance |
| Exact monetary authority | Service invokes `_database_owned_close_projection_evidence()` while holding the lifecycle lock and hashes its source-population identities and exact bridge fields | Existing real PostgreSQL population/bridge tests plus lifecycle bridge-failure regression |
| Currency authority | The locked `reconciliation_run` row supplies `currency_code` to the transition snapshot digest; the close-projection helper is not treated as the owner of run scope | Regression constructs a bridge object with no currency attribute and still hashes successfully when the locked run currency is supplied |
| Replay provenance | Transition row persists statement/book population references and database-owned command hash binds them; `_load_transition_document()` returns those persisted values | Migration contract plus direct replay receipt regression; real PostgreSQL replay must return the same two identities without a bridge rebuild |
| Post-transition immutability | Candidate/match/allocation/approval/exception trigger paths acquire the same run lock and reject writes when run is reconciled | PostgreSQL late-exception insert must fail after supported transition |
| Atomic publication evidence | Transition command, status update, and `reconciliation_run_reconciled` outbox row share one transaction | PostgreSQL test reads all three after command completion |

## Current-head causal repairs

### Advisory-lock snapshot freshness

The original lifecycle implementation used `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` and then acquired the run advisory lock. The implementation and ADR described this as acquiring the lock before the first MVCC snapshot because no domain table had yet been read. That premise was incomplete: the advisory-lock function is invoked by a SQL statement, and in a repeatable-read transaction that first statement can establish the transaction snapshot before it blocks.

A focused RED regression now stops immediately after the lifecycle lock and requires the transaction isolation command to be `READ COMMITTED`. The narrow production repair changes only the lifecycle finalizer's isolation mode and explanatory contract. The lifecycle lock remains the aggregate serialization primitive; run row locking, database state-machine guards, immutable transition evidence, exact bridge reconstruction, maker-checker exception authority, and atomic outbox behavior remain unchanged.

The operational invariant is now falsifiable: a future real PostgreSQL concurrency acceptance must hold the run lifecycle lock in one transaction while committing qualifying reconciliation evidence, start finalization while that lock is held, then prove that after lock grant the finalizer evaluates the committed evidence rather than the pre-wait state. If any authority-bearing evidence mutation can bypass the shared lifecycle lock, this design is invalid and that bypass is itself a P1 defect.

### Run-scope currency ownership

The initial lifecycle implementation computed `reconciliation_snapshot_hash` with `bridge.currency_code`. The dependency-root `_DatabaseOwnedCloseProjectionEvidence` deliberately owns statement/book populations and exact bridge arithmetic but does not expose `currency_code`; currency is already immutable reconciliation-run scope. A real lifecycle execution could therefore reach a fully tied bridge and then fail with an `AttributeError` while building the transition digest, even though unit doubles happened to carry an extra `currency_code` attribute.

The repair follows the existing authority model instead of widening the bridge helper: the already locked `reconciliation_run` query returns `currency_code`, and the transition digest receives that database-owned run-scope value explicitly. A focused regression intentionally omits currency from the bridge object so future refactors cannot silently reintroduce the projection-shape dependency. The compatibility fallback inside the private digest helper exists only for existing direct unit callers; the production lifecycle path passes the locked run currency explicitly.

### Named status-command authority

Review of the sibling completion implementation exposed a second source-real authority gap in this branch: the database trigger originally guarded only transitions whose *new* value was `reconciled`. Migration `0013` intentionally freezes reconciliation scope but does not make `run_status_code` immutable, so a sufficiently privileged SQL session could otherwise move an `evaluating` run directly to `review_required`, `not_reconciled`, or `superseded`, or move a reconciled run away from its evidence-bearing state, without any named lifecycle command or immutable command evidence.

The repair was carried over rather than treating the concurrent branch as a conflict. `enforce_reconciliation_run_reconciled_transition()` permits only a no-op update without a command; every changed target other than `reconciled` raises `reconciliation_lifecycle_target_forbidden`. The existing evidence-derived `reconciled` edge retains its advisory lock, legal predecessor-state check, and exactly-one transition-command requirement. Future `review_required`, `not_reconciled`, or `superseded` behavior must arrive as deliberate named commands with their own evidence and permissions and then explicitly evolve this database state machine. A real PostgreSQL regression attempts raw `evaluating` → `not_reconciled` and requires a database rejection containing `named lifecycle command`.

### Initial-state authority

A further attack path remained after update hardening: because migration `0013` allows the complete status enum on the column and the lifecycle trigger originally fired only on `UPDATE`, a privileged SQL session could attempt to insert a brand-new run already marked `reconciled` (or another non-initial state). The deferred opening-command provenance guard is necessary but is not a substitute for lifecycle state provenance; a forged run plus plausible source-command evidence must never materialize terminal state without traversing the named transition.

The same database state-machine function now handles `INSERT` as well as `UPDATE OF run_status_code`. Every new aggregate must begin as `evaluating`; any other initial status raises `reconciliation_lifecycle_initial_state`. Existing historical rows are not rewritten when migration `0019` installs because the trigger applies only to future row operations. The supported `accept_reconciliation_run()` path already creates `evaluating`, so product behavior is narrowed only for bypass attempts. A real PostgreSQL regression clones valid immutable run scope into a fresh UUID while forcing initial `reconciled` and requires the insert to fail immediately with the initial-state guard.

### Exact replay source-population provenance

The first lifecycle implementation produced a richer first-success document than its exact retry. On first execution, the service appended `statement_population_reference` and `book_population_reference` from the just-reconstructed bridge after loading the transition row. On an idempotent retry, the code returned the stored transition receipt before rebuilding the bridge, but the transition table had never persisted those population identities. The retry was financially non-mutating, yet its response silently dropped the exact source-population provenance that makes a close-evidence receipt independently auditable. Reconstructing a bridge on replay would be worse because a later knowledge state could produce a different population and violate idempotency semantics.

The repair makes the immutable transition command the owner of replay provenance. `reconciliation_run_transition_command` requires both content-addressed population references; PostgreSQL includes them in the database-computed transition-command hash; the service inserts them from the database-owned bridge observed while holding the lifecycle lock; and `_load_transition_document()` returns the persisted values for both first execution and replay. Exact idempotency therefore means stable effects **and** stable source-provenance receipt shape.

### Immutable reconciliation-evidence aggregate membership

A concurrent agent identified a distinct way to weaken the lifecycle freeze: an UPDATE to an evidence row could change `tenant_account_id` or `reconciliation_run_id`, causing the lifecycle guard to inspect only the destination run and allowing a row that originated in a finalized aggregate to masquerade as evidence owned by another run. The concurrent commit introduced the correct invariant in a new `0020` migration, but that file was not yet wired into the canonical migration loader, required-file manifest, or operator install order.

The causal invariant was preserved with TDD and normalized into the still-unreleased migration boundary. A repository RED contract first required the aggregate-membership check to occur before lifecycle-lock selection. `guard_reconciled_run_evidence_mutation()` in unreleased `0019` rejects any UPDATE whose tenant or run identity changes with `reconciliation_lifecycle_scope_immutable`; all existing evidence triggers inherit the strengthened function. The redundant child-only aggregate-membership migration was removed because its behavior was already canonical inside unreleased `0019`. If `0019` reaches protected `develop` before this child, the same change must instead become a forward migration and `0019` must remain untouched.

## Deliberate limitation

The database independently enforces aggregate initial state, aggregate membership, legal status edges, review/exception eligibility, command immutability, serialization, and persistence of the source-population identities used by the service-derived snapshot. The complete `reconciliation_snapshot_hash` is computed by the service from database-owned facts read while it owns the shared lifecycle lock; PostgreSQL binds the digest and the population identities into its own command hash but does not independently rederive every statement/book population identity and monetary bridge component in SQL. Documentation and PR language must preserve this distinction. A later SQL-native derivation would require exact parity/property tests before replacing this boundary.

## Migration sequencing

This lifecycle candidate extends migration `0019_reconciliation_run_command_evidence.sql` only because that migration is still unreleased on the parent dependency-root branch. The child must merge into that parent before the parent reaches protected `develop`. A short-lived concurrent aggregate-membership migration was intentionally removed only after its invariant and test were incorporated into the current unreleased `0019`; it never became protected-branch migration history. If the parent lands first, all child schema changes become forward migrations instead; an already-applied `0019` must never be rewritten.

## Next evidence

The stacked PR is not merge-ready until its exact head passes repository validation, real PostgreSQL tests, complete statement/branch and edge-case coverage, SAST/security/dependency review, reproducible package/SBOM/provenance, and required independent review. In addition to replay and cross-run membership acceptance, real PostgreSQL concurrency must prove that a lifecycle finalizer waiting behind a shared-lock evidence writer sees the writer's committed terminal/review evidence after lock grant rather than a pre-wait snapshot.
