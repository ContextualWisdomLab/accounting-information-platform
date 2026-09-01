# ADR 0060: Evidence-derived reconciliation-run lifecycle authority

- Status: Proposed
- Date: 2026-09-01

## Context

`accept_reconciliation_run()` deliberately opens a tenant-scoped run in `evaluating` state. Close-package construction, however, must reject every run that has not lawfully reached `reconciled`. A direct SQL status rewrite is not an owner-control path because it can bypass application idempotency, actor/purpose evidence, exact source-population reconstruction, review completeness, exception state, and transactional outbox evidence.

The lifecycle transition is part of the **Reconciliation Review** supporting subdomain. It does not expand Accounting Record & Close authority: a reconciled run remains evidence for a separately authorized period-close decision and cannot post or reverse a journal, mutate accounting policy, or close a fiscal period by itself.

## Decision

The `reconciliation_run` aggregate owns the legal transition from `evaluating` or `review_required` to `reconciled`. The transition is accepted only through a tenant-scoped, idempotent lifecycle command whose immutable evidence includes the run identity, target status, exact reconciliation snapshot hash, actor reference, purpose code, effective time, and database-owned command hash.

No changed `run_status_code` is a generic database field edit. This migration introduces exactly one named status-changing command, the evidence-derived transition to `reconciled`; every other changed target is rejected by the database until a future named command defines its own legal predecessor states, evidence, authorization, idempotency, and audit/outbox contract and deliberately evolves the state-machine guard. A no-op assignment of the current status remains harmless. This prevents a privileged SQL caller from manufacturing `review_required`, `not_reconciled`, `superseded`, or a post-reconciliation downgrade without command evidence.

The application performs the authority-bearing read under one PostgreSQL `REPEATABLE READ` transaction. It executes `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ`, acquires the run lifecycle transaction advisory lock before the first data query establishes the MVCC snapshot, then reads the run, review population, exception population, immutable opening-command evidence, and the database-owned statement/book populations used by the exact book-to-bank bridge. The transition fails closed when:

- the run is absent, terminal, or already reconciled under another command;
- the lifecycle idempotency key was used for different evidence or was already used as the run-opening command key;
- any match remains `proposed`;
- an `approved` or `rejected` match lacks decision-consistent immutable approval evidence;
- any reconciliation exception remains `open`;
- immutable opening-command provenance is missing; or
- the database-owned exact bridge cannot tie without an unexplained difference.

A successful transaction writes `reconciliation_run_transition_command`, updates the run status to `reconciled`, and appends the `reconciliation_run_reconciled` transactional-outbox event atomically. Exact retries replay the immutable transition command instead of creating another transition.

Migration `0019_reconciliation_run_command_evidence.sql` is still unreleased on the dependency-root branch, so this stacked slice extends that migration rather than creating a later migration. The child branch **must integrate into the dependency-root branch before that migration reaches protected `develop`**. If the parent reaches protected `develop` first, this design must be rewritten as a forward `0020` migration; an applied migration must never be edited in place.

## Database authority and concurrency invariants

PostgreSQL independently enforces the legal state edge and evidence serialization:

1. `reconciliation_run_transition_command` is tenant-scoped, forced-RLS, immutable command evidence with at most one `reconciled` transition per run.
2. A database trigger recomputes the transition-command hash from the opening command, run/tenant identity, target status, snapshot hash, actor, purpose, effective time, and idempotency identity.
3. A changed `run_status_code` is rejected unless it is the supported `reconciled` target backed by exactly one transition command in the same transaction. All other changed targets require a future named lifecycle command and are fail-closed today.
4. Candidate, match, allocation, approval, and exception writes acquire the same run lifecycle transaction advisory lock. Once the run is `reconciled`, reviewed evidence is frozen and corrections require a new/superseding run rather than mutation behind existing close evidence.
5. The transition insertion independently checks for proposed matches, open exceptions, and terminal approval/snapshot consistency before it can authorize the status update.

The service computes `reconciliation_snapshot_hash` from database-owned source facts and exact Decimal bridge values observed in the protected snapshot. The database stores and binds that digest but does **not** independently rederive every bridge component inside SQL in this slice. This limitation is deliberate and must not be represented as database-side recomputation of the complete monetary bridge. Moving snapshot derivation fully into PostgreSQL is a future option only if parity/property tests prove exact equivalence with the domain representation.

## DDD mapping

- **Subdomain:** Reconciliation Review (supporting).
- **Aggregate root:** `reconciliation_run`.
- **Command evidence entity:** `reconciliation_run_transition_command`.
- **Value evidence:** lifecycle idempotency key, target status, exact reconciliation snapshot hash, actor reference, purpose code, effective time, command hash.
- **Domain event:** `reconciliation_run_reconciled` through the accounting transactional outbox.
- **Domain service:** `reconcile_reconciliation_run()` reconstructs eligibility from repositories/database-owned facts and performs the transition transaction.
- **Invariant:** every changed run status requires a named command; `reconciled` specifically means one reviewed run whose exact source bridge ties, whose terminal matches carry current immutable decisions, whose exceptions are not open, and whose transition is backed by one immutable command.
- **Anti-corruption boundary:** bank evidence remains non-posting input; external billing, identity, architecture, and orchestration contexts cannot write reconciliation or accounting tables directly.

The lifecycle aggregate remains separate from the period-close aggregate. This avoids making statement ingestion, matching, reconciliation review, journal posting, and period close one oversized transaction boundary.

## Consequences

Controllers gain a supported repository-owned path from run evaluation to review-complete reconciliation. Direct status SQL no longer constitutes a valid product operation. A reconciled run is stable enough to become close-package evidence because later review-population mutation is rejected.

The public surface introduced here is the package API. A buyer-facing HTTP lifecycle route should be added only with the purpose-bound authorization integration so the route cannot create an unauthenticated high-impact control path. Until that integration lands, this ADR does not claim that a controller HTTP endpoint exists.

This slice does not yet introduce a dedicated PostgreSQL login/capability role solely for reconciliation completion. The database state machine and immutable command evidence are authoritative even for a privileged session, while deployment-level least-privilege credentials remain a separate operability/security hardening lane to be integrated with the purpose-bound application authorization surface rather than silently granting a generic runtime login status-edit authority.

## Verification

Acceptance evidence must bind to one unchanged exact head and include:

- unit tests for input validation, exact replay/conflict, legal states, match/approval completeness, open exceptions, bridge failure, missing provenance, deterministic snapshot binding, and lock ordering;
- real PostgreSQL tests proving raw `UPDATE ... SET run_status_code='reconciled'` fails without lifecycle command evidence, raw SQL cannot manufacture another changed lifecycle target without its own named command, the supported command writes transition + status + outbox atomically, exact replay is idempotent, and reviewed evidence freezes after reconciliation;
- existing real PostgreSQL close-projection tests proving statement/book populations and exact bridge values are database-derived under the correct accounting-book scope;
- exact 100% owned production statement/branch coverage and public-docstring/repository contracts; and
- current-head CI, SAST, security/dependency, reproducibility/SBOM/provenance, and required review evidence.

## Research basis

PostgreSQL `REPEATABLE READ` uses a transaction snapshot established at the first non-transaction-control statement, so acquiring the lifecycle advisory lock before the first data read prevents the transition from observing a snapshot that predates a concurrent evidence writer which is already serialized on the same lock. Row locks protect the run state from conflicting writers, while transaction-level advisory locks provide an application-defined coordination primitive that is released automatically at transaction end.

### References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html
