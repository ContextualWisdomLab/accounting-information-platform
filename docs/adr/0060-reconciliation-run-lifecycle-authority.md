# ADR 0060: Evidence-derived reconciliation-run lifecycle authority

- Status: Proposed
- Date: 2026-09-01

## Context

`accept_reconciliation_run()` deliberately opens a tenant-scoped run in `evaluating` state. Close-package construction, however, must reject every run that has not lawfully reached `reconciled`. A direct SQL status rewrite or terminal-state insert is not an owner-control path because it can bypass application idempotency, actor/purpose evidence, exact source-population reconstruction, review completeness, exception state, and transactional outbox evidence.

The lifecycle transition is part of the **Reconciliation Review** supporting subdomain. It does not expand Accounting Record & Close authority: a reconciled run remains evidence for a separately authorized period-close decision and cannot post or reverse a journal, mutate accounting policy, or close a fiscal period by itself.

## Decision

The `reconciliation_run` aggregate is created only in `evaluating` state and owns the legal transition from `evaluating` or `review_required` to `reconciled`. The transition is accepted only through a tenant-scoped, idempotent lifecycle command whose immutable evidence includes the run identity, target status, exact reconciliation snapshot hash, exact statement-population identity, exact book-population identity, actor reference, purpose code, effective time, and database-owned command hash.

No initial or changed `run_status_code` is a generic database field edit. On aggregate insertion the database rejects every initial value other than `evaluating`; this prevents a privileged SQL caller from creating a pre-reconciled or otherwise terminal run that never traversed the lifecycle command boundary. After creation, this migration introduces exactly one named status-changing command, the evidence-derived transition to `reconciled`; every other changed target is rejected by the database until a future named command defines its own legal predecessor states, evidence, authorization, idempotency, and audit/outbox contract and deliberately evolves the state-machine guard. A no-op assignment of the current status remains harmless. Together these rules prevent raw SQL from manufacturing `review_required`, `not_reconciled`, `superseded`, `reconciled`, or a post-reconciliation downgrade without the corresponding command authority.

Review evidence also has immutable aggregate membership. Candidate, match, statement allocation, journal allocation, approval, and exception rows may not change either `tenant_account_id` or `reconciliation_run_id` after creation. Without that invariant, a privileged writer could evade a reconciled run's evidence freeze by reassigning an existing row to another evaluating run before or after mutating it. Corrections are new/superseding evidence in the destination run, never cross-aggregate row reassignment.

The application performs the authority-bearing read under one PostgreSQL `READ COMMITTED` transaction. It executes `SET TRANSACTION ISOLATION LEVEL READ COMMITTED`, then acquires the shared run lifecycle transaction advisory lock before tenant, run, review, exception, opening-command, statement-population, or book-population reads. This order is deliberate: the advisory-lock call is itself a SQL statement. Under `REPEATABLE READ`, that lock statement can establish a transaction snapshot before waiting, so a finalizer that waits behind a guarded evidence writer can retain a snapshot that predates the writer's commit after the lock is granted. Under `READ COMMITTED`, each later authority query receives a fresh statement snapshot; therefore a commit completed by the prior lock holder is visible after the wait. Every supported reconciliation-evidence mutation path uses the same run lifecycle lock, so once finalization owns the lock no later guarded aggregate mutation can race the authority reads and status/outbox write.

The transition fails closed when:

- the run is absent, terminal, or already reconciled under another command;
- the lifecycle idempotency key was used for different evidence or was already used as the run-opening command key;
- any match remains `proposed`;
- an `approved` or `rejected` match lacks decision-consistent immutable approval evidence;
- any reconciliation exception remains `open` or lacks matching immutable maker-checker resolution-command evidence;
- immutable opening-command provenance is missing; or
- the database-owned exact bridge cannot tie without an unexplained difference.

A successful transaction writes `reconciliation_run_transition_command`, including the exact statement/book population references used by the bridge, updates the run status to `reconciled`, and appends the `reconciliation_run_reconciled` transactional-outbox event atomically. The database-computed transition-command hash also binds both population references. Exact retries replay the immutable transition command and therefore return the same source-population provenance without re-reading or reconstructing a newer bridge. The externally visible success/replay receipt shape is thus stable under an unchanged idempotency key rather than returning population provenance only on first execution.

Migration `0019_reconciliation_run_command_evidence.sql` is still unreleased on the dependency-root branch, so this stacked slice extends that migration rather than creating a later migration. A concurrent agent briefly introduced `0020_reconciliation_evidence_aggregate_membership.sql` to strengthen the same lifecycle guard. Its causal invariant was preserved, with a repository contract, by folding the guard into the still-unreleased `0019`; the redundant forward file was then removed so the canonical loader/docs cannot silently omit a child-only migration. The child branch **must integrate into the dependency-root branch before `0019` reaches protected `develop`**. If the parent reaches protected `develop` first, all child schema changes must become forward migrations; an applied migration must never be edited in place.

## Database authority and concurrency invariants

PostgreSQL independently enforces the initial state, legal transition edge, evidence membership, and evidence serialization:

1. A new `reconciliation_run` must begin in `evaluating`; direct insertion of `review_required`, `reconciled`, `not_reconciled`, or `superseded` is rejected before deferred provenance can make the row durable.
2. `reconciliation_run_transition_command` is tenant-scoped, forced-RLS, immutable command evidence with at most one `reconciled` transition per run and durable statement/book population identities.
3. A database trigger recomputes the transition-command hash from the opening command, run/tenant identity, target status, snapshot hash, statement population, book population, actor, purpose, effective time, and idempotency identity.
4. A changed `run_status_code` is rejected unless it is the supported `reconciled` target backed by exactly one transition command in the same transaction. All other changed targets require a future named lifecycle command and are fail-closed today.
5. Candidate, match, allocation, approval, exception, and exception-resolution paths acquire the same run lifecycle transaction advisory lock. Their tenant/run aggregate membership is immutable. `READ COMMITTED` is used by finalization so a wait on that lock cannot pin a pre-wait transaction snapshot; after acquisition, later authority reads observe the preceding guarded writer's commit. Once the run is `reconciled`, reviewed evidence is frozen and corrections require a new/superseding run rather than mutation behind existing close evidence.
6. The transition insertion independently checks for proposed matches, unresolved or unauthoritatively terminal exceptions, and terminal approval/snapshot consistency before it can authorize the status update.
7. Exact replay reads the persisted transition evidence, including source-population identities; it does not recalculate those identities against a later database state.

The service computes `reconciliation_snapshot_hash` from database-owned source facts and exact Decimal bridge values observed while it owns the lifecycle lock. The database stores and binds that digest but does **not** independently rederive every bridge component inside SQL in this slice. This limitation is deliberate and must not be represented as database-side recomputation of the complete monetary bridge. Moving snapshot derivation fully into PostgreSQL is a future option only if parity/property tests prove exact equivalence with the domain representation.

## DDD mapping

- **Subdomain:** Reconciliation Review (supporting).
- **Aggregate root:** `reconciliation_run`.
- **Command evidence entity:** `reconciliation_run_transition_command`.
- **Aggregate membership invariant:** existing review-evidence rows never move between tenant/run aggregates; destination corrections are separate new/superseding evidence.
- **Value evidence:** lifecycle idempotency key, target status, exact reconciliation snapshot hash, statement population reference, book population reference, actor reference, purpose code, effective time, command hash.
- **Domain event:** `reconciliation_run_reconciled` through the accounting transactional outbox.
- **Domain service:** `reconcile_reconciliation_run()` reconstructs eligibility from repositories/database-owned facts and performs the transition transaction.
- **Invariant:** a run is born in `evaluating`, every later changed run status requires a named command, and `reconciled` specifically means one reviewed run whose exact source bridge ties, whose source population identities are durably retained for replay, whose terminal matches carry current immutable decisions, whose exceptions have matching immutable maker-checker command evidence, and whose transition is backed by one immutable command.
- **Anti-corruption boundary:** bank evidence remains non-posting input; external billing, identity, architecture, and orchestration contexts cannot write reconciliation or accounting tables directly.

The lifecycle aggregate remains separate from the period-close aggregate. This avoids making statement ingestion, matching, reconciliation review, journal posting, and period close one oversized transaction boundary.

## Consequences

Controllers gain a supported repository-owned path from run evaluation to review-complete reconciliation. Direct terminal insertion and direct status SQL no longer constitute valid product operations. A reconciled run is stable enough to become close-package evidence because later review-population mutation and cross-run evidence reassignment are rejected, and an exact replay can reproduce the same source-population references without observing later statement/journal data.

`READ COMMITTED` is an intentional concurrency choice for the finalization service, not a weakening of accounting consistency. The lifecycle advisory lock is the aggregate serialization primitive. The isolation level ensures that the statement executed after a lock wait sees the commit that released the lock instead of retaining a pre-wait snapshot. This contract depends on every authority-bearing reconciliation evidence writer retaining the shared lifecycle-lock guard; any path that can mutate eligible evidence without that lock is a P1 integration defect.

The public surface introduced here is the package API. A buyer-facing HTTP lifecycle route should be added only with the purpose-bound authorization integration so the route cannot create an unauthenticated high-impact control path. Until that integration lands, this ADR does not claim that a controller HTTP endpoint exists.

This slice does not yet introduce a dedicated PostgreSQL login/capability role solely for reconciliation completion. The database state machine and immutable command evidence are authoritative even for a privileged session, while deployment-level least-privilege credentials remain a separate operability/security hardening lane to be integrated with the purpose-bound application authorization surface rather than silently granting a generic runtime login status-edit authority.

## Verification

Acceptance evidence must bind to one unchanged exact head and include:

- unit tests for input validation, exact replay/conflict, replayed source-population provenance, legal states, match/approval completeness, maker-checker exception authority, bridge failure, missing provenance, deterministic snapshot binding, and lock ordering;
- an isolation regression proving lifecycle finalization configures `READ COMMITTED` before taking the shared run lifecycle lock, so subsequent authority reads can observe a commit completed while that lock was awaited;
- migration/repository contracts proving statement and book population references are required immutable transition evidence, are included in the database-owned transition-command digest, and review-evidence tenant/run membership cannot be reassigned;
- real PostgreSQL tests proving a raw terminal-state run `INSERT` fails, raw `UPDATE ... SET run_status_code='reconciled'` fails without lifecycle command evidence, raw SQL cannot manufacture another changed lifecycle target without its own named command, cross-run evidence reassignment fails, the supported command writes transition + status + outbox atomically, exact replay is idempotent and returns the same source-population provenance, and reviewed evidence freezes after reconciliation;
- a real PostgreSQL concurrency test in which finalization waits behind a lifecycle-lock-protected evidence mutation and, after lock grant, evaluates the committed evidence rather than a pre-wait snapshot;
- existing real PostgreSQL close-projection tests proving statement/book populations and exact bridge values are database-derived under the correct accounting-book scope;
- exact 100% owned production statement/branch coverage and public-docstring/repository contracts; and
- current-head CI, SAST, security/dependency, reproducibility/SBOM/provenance, and required review evidence.

## Research basis

PostgreSQL `READ COMMITTED` starts each command with a snapshot of rows committed before that command began. `REPEATABLE READ`, by contrast, fixes the transaction snapshot from the first non-transaction-control statement. Because `pg_advisory_xact_lock(...)` is invoked by a SQL statement, using it as the first statement in a `REPEATABLE READ` transaction can pin a pre-wait snapshot. The lifecycle service therefore combines the shared transaction-level advisory lock with `READ COMMITTED`: a waiter acquires the lock only after the preceding guarded writer commits/releases it, and the later authority queries take snapshots after that point. Row locks continue to protect the run state from conflicting writers, while transaction-level advisory locks provide the wider application-defined aggregate coordination primitive and release automatically at transaction end.

### References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html
