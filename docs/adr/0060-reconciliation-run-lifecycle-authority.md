# ADR 0060: Evidence-derived reconciliation-run lifecycle authority

- Status: Proposed
- Date: 2026-09-01
- Updated: 2026-09-02

## Context

`accept_reconciliation_run()` deliberately opens a tenant-scoped run in `evaluating` state. Close-package construction must reject every run that has not lawfully reached `reconciled`. A direct SQL status rewrite or terminal-state insert is not an owner-control path because it can bypass application idempotency, actor/purpose evidence, exact source-population reconstruction, review completeness, exception authority, and transactional outbox evidence.

The lifecycle transition belongs to the **Reconciliation Review** supporting subdomain. It does not expand Accounting Record & Close authority: a reconciled run remains evidence for a separately authorized period-close decision and cannot post or reverse a journal, mutate accounting policy, or close a fiscal period by itself.

The source evidence used by reconciliation is broader than the mutable review aggregate. Bank-statement facts are append-only integration evidence and posted cash-journal facts are append-only General Ledger evidence. Both can contribute to the exact book-to-bank population. Consequently, serializing only reconciliation-review writers is insufficient if lifecycle finalization reads those source populations through multiple SQL statements.

## Decision

The `reconciliation_run` aggregate is created only in `evaluating` state and owns the legal transition from `evaluating` or `review_required` to `reconciled`. The transition is accepted only through a tenant-scoped, idempotent lifecycle command whose immutable evidence includes the run identity, target status, database-derived reconciliation snapshot, exact statement-population identity, exact book-population identity, actor reference, purpose code, effective time, and database-owned command hash.

No initial or changed `run_status_code` is a generic database field edit. On aggregate insertion the database rejects every initial value other than `evaluating`. After creation, this migration introduces exactly one named status-changing command, the evidence-derived transition to `reconciled`; every other changed target is rejected until a future named command defines its legal predecessor states, evidence, authorization, idempotency, audit/outbox contract, and state-machine change. A no-op assignment of the current status remains harmless.

Review evidence has immutable aggregate membership. Candidate, match, statement allocation, journal allocation, approval, exception, and exception-resolution rows may not move between tenant/run aggregates. Corrections are new or superseding evidence, never cross-aggregate reassignment.

### Post-lock coherent PostgreSQL snapshot

Lifecycle finalization uses one PostgreSQL session in two transaction phases:

1. The session acquires `pg_advisory_lock(hashtext(tenant_reference), hashtext(reconciliation_run_lifecycle:<run_id>))` and commits that preliminary transaction. The lock is session-scoped, so the commit does not release it. If another guarded reconciliation writer holds the corresponding transaction advisory lock, finalization waits **before** the accounting-authority snapshot exists.
2. On the same session, finalization starts a fresh transaction with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ`, reacquires the same transaction-level lifecycle lock reentrantly, and only then reads tenant identity, run state, review/approval state, exception-resolution evidence, opening-command provenance, bank-statement populations, posted cash-journal populations, and exact bridge arithmetic. The transition command, `reconciled` status, and outbox event are committed from that same coherent snapshot. Only after that commit is the session advisory lock released.

This two-phase lock/snapshot order is required because `pg_advisory_xact_lock(...)` is itself executed by a SQL statement. If a transaction-level advisory-lock statement is the first non-transaction-control statement in a `REPEATABLE READ` transaction, PostgreSQL can establish the transaction snapshot before the lock wait completes. A waiter can then retain a pre-wait snapshot even after the previous lock holder commits. Conversely, `READ COMMITTED` would solve that visibility problem but would permit sequential authority queries to observe different statement snapshots, including later bank-statement or posted-journal facts. The session-lock-before-transaction design provides both post-wait visibility and one coherent source/review snapshot.

The transition fails closed when:

- the run is absent, terminal, or already reconciled under another command;
- the lifecycle idempotency key was used for different evidence or was already used as the run-opening command key;
- any match remains `proposed`;
- an `approved` or `rejected` match lacks decision-consistent immutable approval evidence;
- any reconciliation exception remains `open` or lacks matching immutable maker-checker resolution-command evidence;
- immutable opening-command provenance is missing;
- statement opening plus admitted movements does not equal statement closing;
- approved statement or journal allocations reference an unknown source or exceed database-owned source capacity; or
- the database-owned exact bridge cannot tie without an unexplained difference.

A successful transaction writes `reconciliation_run_transition_command`, including the exact statement/book population references used by the bridge, updates the run status to `reconciled`, and appends `reconciliation_run_reconciled` to the transactional outbox atomically. The database-computed transition-command hash binds the final database-derived snapshot and both population references. Exact retries replay persisted transition evidence and do not rebuild a newer bridge.

The dependency parent owns `0019_reconciliation_run_database_snapshot_authority.sql`, an unreleased authority overlay applied after the base 0019 migration. Child migration `0020_reconciliation_exception_resolution_command.sql` adds the named maker-checker exception command, and forward migration `0021_reconciliation_exception_resolution_outbox_pair.sql` adds the deferred resolution-outbox pair plus the child resolution-evidence snapshot overlay. These stacked migrations must integrate dependency-first before they reach protected `develop`; once a migration is applied from a protected/released source, later changes must be forward migrations.

### Database-owned transition snapshot

The application still reconstructs the exact source populations and bridge before attempting the lifecycle command. That is an early fail-closed domain check, not the final database authority.

The parent overlay defines `accounting_core.reconciliation_run_database_snapshot_authority(tenant_id, run_id)`. It independently reconstructs the run/opening-command scope, retained opening/closing bank balances, complete immutable statement-entry population, assigned cash-account journal population, approved statement/journal allocations, reviewed match/approval state, and exception state. It verifies source identity/cardinality, run currency, exact statement arithmetic, source-capacity conservation, book opening/period/closing arithmetic and the final book-to-bank equation, then returns PostgreSQL-owned snapshot, statement-population, and book-population identities.

`accounting_reconciliation_transition_database_authority_guard` is the parent's `BEFORE INSERT` trigger on `reconciliation_run_transition_command`. It replaces all three caller-owned authority values before the transition command hash is assigned. The child 0021 overlay then runs `accounting_reconciliation_transition_evidence_snapshot_guard`, which calls the parent authority again and composes the immutable exception-resolution command population into the final `reconciliation_snapshot_hash` while preserving the parent-owned statement/book references. The existing `accounting_reconciliation_transition_hash_guard` runs afterward and binds that final database-owned snapshot plus both population identities into the immutable transition-command hash.

PostgreSQL executes same-kind triggers in lexical name order. The exact trigger names deliberately sort `...database_authority_guard` before `...evidence_snapshot_guard` before `...hash_guard`. A privileged direct INSERT therefore cannot promote caller-selected snapshot/population values, and the child cannot weaken the stronger parent database bridge while adding maker-checker resolution evidence.

The server-native snapshot deliberately need not equal the Python serialization digest. They are separate defense-in-depth identities over the same accounting facts: the application digest proves the supported command reconstructed a coherent source/review snapshot, while PostgreSQL independently prevents direct-SQL authority substitution. The persisted transition row and transition-command hash use the final database-owned stacked snapshot.

## Database authority and concurrency invariants

1. A new `reconciliation_run` begins in `evaluating`; direct insertion of a terminal or review state is rejected.
2. `reconciliation_run_transition_command` is tenant-scoped, forced-RLS, immutable command evidence with at most one `reconciled` transition per run and durable statement/book population identities.
3. The parent PostgreSQL authority overlay derives source/bridge snapshot and both population identities from database-owned facts. The child 0021 overlay composes immutable resolution-command evidence into the final snapshot without changing those parent population identities. PostgreSQL then recomputes the transition-command hash from opening command, run/tenant identity, target status, final server snapshot, source-population references, actor, purpose, effective time, and idempotency identity.
4. A changed `run_status_code` is rejected unless it is the supported `reconciled` target backed by exactly one transition command in the same transaction. Other changed targets require their own future named commands.
5. Candidate, match, allocation, approval, exception, and exception-resolution mutation paths use the shared run lifecycle transaction lock and cannot move evidence across aggregate scope.
6. Finalization owns the corresponding **session** lifecycle lock before beginning its repeatable-read authority transaction, so a preceding guarded writer is visible and a later guarded writer cannot interleave before transition commit.
7. Bank-statement and posted-journal facts are not required to acquire the reconciliation lifecycle lock. Their application contribution is coherent because all source/review queries used for finalization execute in the same post-lock `REPEATABLE READ` snapshot; the database authority overlay independently derives its transition evidence from database-owned facts at transition insertion.
8. Exact replay reads persisted transition evidence, including the final database snapshot and source-population identities; it does not recalculate those identities against later database state.

## DDD mapping

- **Subdomain:** Reconciliation Review (supporting).
- **Aggregate root:** `reconciliation_run`.
- **Command evidence entity:** `reconciliation_run_transition_command`.
- **Aggregate membership invariant:** existing review evidence never moves between tenant/run aggregates.
- **Value evidence:** lifecycle idempotency key, target status, database-derived reconciliation snapshot hash, statement/book population references, actor, purpose, effective time, command hash.
- **Domain event:** `reconciliation_run_reconciled` through the accounting transactional outbox.
- **Domain service:** `reconcile_reconciliation_run()` reconstructs eligibility from repositories/database-owned facts and performs the transition transaction.
- **Anti-corruption boundary:** bank evidence remains non-posting input; external billing, identity, architecture, and orchestration contexts cannot write reconciliation or accounting tables directly.

The lifecycle aggregate remains separate from the period-close aggregate. Statement ingestion, matching, reconciliation review, journal posting, and period close are not one oversized transaction boundary.

## Consequences

Controllers gain a supported repository-owned path from evaluation to review-complete reconciliation. Direct terminal insertion and direct status SQL are not valid product operations. A reconciled run is stable close evidence because review facts are frozen, exact replay preserves source identities, and finalization is derived from one coherent post-lock source/review snapshot plus independently database-owned transition evidence.

The session-level advisory lock is intentionally held across the complete repeatable-read authority transaction and its commit. This slightly extends the lifetime of the run-scoped lock but avoids both a pre-wait repeatable-read snapshot and a mixed read-committed snapshot. The repository's five-second `lock_timeout` remains the fail-closed bound for contended lock acquisition.

The public surface introduced here is the package API. A buyer-facing HTTP lifecycle route should be added only with purpose-bound authorization integration; this ADR does not claim that such an authenticated controller endpoint exists. A dedicated least-privilege PostgreSQL capability for reconciliation completion remains a separate security/operability slice.

## Verification

Acceptance evidence must bind to one unchanged exact head and include:

- unit tests for input validation, exact replay/conflict, source-population provenance, legal states, match/approval completeness, maker-checker exception authority, bridge failure, missing provenance, deterministic snapshot binding, and session/transaction lock ordering;
- a focused contract proving session advisory lock acquisition and commit precede the fresh `REPEATABLE READ` authority transaction;
- a real PostgreSQL concurrency test in which finalization waits behind a lifecycle-lock-protected exception writer and, after lock grant, observes the committed resolution;
- a real PostgreSQL source-snapshot test that inserts otherwise eligible bank-statement source facts after review state has been read and proves those later rows cannot enter the same finalization's statement population;
- the parent database-authority contract plus `tests/test_reconciliation_lifecycle_database_authority_postgres.py`, proving caller-selected authority identities are replaced and an untied database bridge cannot become transition evidence;
- `tests/test_reconciliation_resolution_snapshot_overlay_contract.py`, proving parent authority runs before child resolution evidence and before the final command hash;
- migration/repository contracts for immutable transition evidence, aggregate membership, lifecycle state guards, exception maker-checker authority, and both database snapshot triggers;
- real PostgreSQL tests for raw terminal-state rejection, raw status-update rejection, transition/status/outbox atomicity, exact replay, and post-reconciliation evidence freeze;
- existing real PostgreSQL close-projection tests proving statement/book populations and exact bridge values are database-derived under the correct accounting-book scope;
- exact 100% owned production statement/branch coverage and public-docstring/repository contracts; and
- current-head CI, SAST, security/dependency, reproducibility/SBOM/provenance, and qualifying independent review evidence.

## Research basis

PostgreSQL `READ COMMITTED` starts each command with a snapshot of rows committed before that command begins. `REPEATABLE READ` fixes one transaction snapshot from the first non-transaction-control statement. PostgreSQL advisory locks can be session-level or transaction-level; session-level locks survive transaction boundaries until explicitly released or the session ends, while transaction-level locks release automatically at transaction end. PostgreSQL also executes multiple triggers of the same kind for the same event in alphabetical order by trigger name. The selected design uses those semantics deliberately: acquire the session-level run lock first, commit the preliminary transaction without releasing that lock, begin the repeatable-read authority transaction, then apply parent database authority and child immutable resolution evidence before the final transition command hash is assigned.

### References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
