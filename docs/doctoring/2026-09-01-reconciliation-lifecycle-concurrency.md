# Doctoring record: reconciliation lifecycle concurrency and status authority

**Date:** 2026-09-01  
**Scope:** stacked reconciliation lifecycle candidate on `accounting-information-platform`

## Research question

What concurrency and transaction contract is required so a reconciliation run can move from `evaluating`/`review_required` to `reconciled` without blessing stale, partially reviewed, caller-shaped, pre-terminal, or replay-inconsistent accounting evidence?

## Authoritative findings

PostgreSQL 18 documents that `REPEATABLE READ` uses a transaction snapshot and that the snapshot is established when the first non-transaction-control statement begins. `SET TRANSACTION` configures isolation but is not itself the source-data read. Therefore the lifecycle path sets `REPEATABLE READ`, acquires the transaction advisory lock for the run, and only then performs the tenant/run/source/review queries. A competing evidence mutation path uses the same transaction-level advisory lock, so the transition either observes the completed writer in its later snapshot or completes before the later writer; it does not validate one snapshot and then allow a concurrent reviewed-evidence mutation behind the status change.

`SELECT ... FOR UPDATE` is retained for the `reconciliation_run` row so conflicting status writers serialize on the aggregate state. PostgreSQL transaction-level advisory locks are used for the wider application-defined run boundary because candidates, matches, allocations, approvals, and exceptions reside in different tables. Transaction-level advisory locks release automatically when the transaction ends.

### References (APA 7th)

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SELECT*. https://www.postgresql.org/docs/18/sql-select.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html

## Code-to-evidence traceability

| Requirement | Implementation boundary | Falsifiable evidence |
| --- | --- | --- |
| One coherent authority snapshot | `reconcile_reconciliation_run()` sets `REPEATABLE READ`, acquires `reconciliation_run_lifecycle:<run_id>` before the first data query, then reads run/review/source state | Unit SQL-order assertion plus exact PostgreSQL lifecycle test and existing database-owned close-projection tests |
| Aggregate initial state | Migration lifecycle trigger rejects every new `reconciliation_run` whose initial `run_status_code` is not `evaluating` | Real PostgreSQL raw `INSERT ... run_status_code='reconciled'` must fail with `reconciliation_lifecycle_initial_state` before deferred provenance could make a forged terminal run durable |
| Legal state edge | `reconciliation_run` row lock + migration status guard permits only `evaluating`/`review_required` → `reconciled`; every other changed target is rejected until a separate named command evolves the state machine | Raw SQL into `reconciled` without transition evidence and raw SQL into `not_reconciled` without a named command must both fail |
| Durable command identity | `reconciliation_run_transition_command` stores tenant/run/idempotency/snapshot/statement population/book population/actor/purpose/effective time and database-owned command hash | Exact replay returns the same receipt and source-population references; changed key evidence conflicts |
| Review completeness | Service and database transition trigger reject proposed matches and approved/rejected matches without decision-consistent approval snapshot | Unit cases and migration trigger contract |
| Exception completeness | Service and DB trigger reject `resolution_status_code='open'` | Unit + PostgreSQL evidence-freeze path |
| Exact monetary authority | Service invokes `_database_owned_close_projection_evidence()` inside the lifecycle transaction and hashes its source-population identities and exact bridge fields | Existing real PostgreSQL population/bridge tests plus lifecycle bridge-failure regression |
| Currency authority | The locked `reconciliation_run` row supplies `currency_code` to the transition snapshot digest; the close-projection helper is not treated as the owner of run scope | Regression constructs a bridge object with no currency attribute and still hashes successfully when the locked run currency is supplied |
| Replay provenance | Transition row persists statement/book population references and database-owned command hash binds them; `_load_transition_document()` returns those persisted values | Migration contract plus direct replay receipt regression; real PostgreSQL replay must return the same two identities without a bridge rebuild |
| Post-transition immutability | Candidate/match/allocation/approval/exception trigger paths acquire the same run lock and reject writes when run is reconciled | PostgreSQL late-exception insert must fail after supported transition |
| Atomic publication evidence | Transition command, status update, and `reconciliation_run_reconciled` outbox row share one transaction | PostgreSQL test reads all three after command completion |

## Current-head causal repairs

### Run-scope currency ownership

The initial lifecycle implementation computed `reconciliation_snapshot_hash` with `bridge.currency_code`. The dependency-root `_DatabaseOwnedCloseProjectionEvidence` deliberately owns statement/book populations and exact bridge arithmetic but does not expose `currency_code`; currency is already immutable reconciliation-run scope. A real lifecycle execution could therefore reach a fully tied bridge and then fail with an `AttributeError` while building the transition digest, even though unit doubles happened to carry an extra `currency_code` attribute.

The repair follows the existing authority model instead of widening the bridge helper: the already locked `reconciliation_run` query now returns `currency_code`, and the transition digest receives that database-owned run-scope value explicitly. A focused regression intentionally omits currency from the bridge object so future refactors cannot silently reintroduce the projection-shape dependency. The compatibility fallback inside the private digest helper exists only for existing direct unit callers; the production lifecycle path passes the locked run currency explicitly.

### Named status-command authority

Review of the sibling completion implementation exposed a second source-real authority gap in this branch: the database trigger originally guarded only transitions whose *new* value was `reconciled`. Migration `0013` intentionally freezes reconciliation scope but does not make `run_status_code` immutable, so a sufficiently privileged SQL session could otherwise move an `evaluating` run directly to `review_required`, `not_reconciled`, or `superseded`, or move a reconciled run away from its evidence-bearing state, without any named lifecycle command or immutable command evidence.

The repair was carried over rather than treating the concurrent branch as a conflict. `enforce_reconciliation_run_reconciled_transition()` permits only a no-op update without a command; every changed target other than `reconciled` raises `reconciliation_lifecycle_target_forbidden`. The existing evidence-derived `reconciled` edge retains its advisory lock, legal predecessor-state check, and exactly-one transition-command requirement. Future `review_required`, `not_reconciled`, or `superseded` behavior must arrive as deliberate named commands with their own evidence and permissions and then explicitly evolve this database state machine. A real PostgreSQL regression attempts raw `evaluating` → `not_reconciled` and requires a database rejection containing `named lifecycle command`.

### Initial-state authority

A further attack path remained after update hardening: because migration `0013` allows the complete status enum on the column and the lifecycle trigger originally fired only on `UPDATE`, a privileged SQL session could attempt to insert a brand-new run already marked `reconciled` (or another non-initial state). The deferred opening-command provenance guard is necessary but is not a substitute for lifecycle state provenance; a forged run plus plausible source-command evidence must never materialize terminal state without traversing the named transition.

The same database state-machine function now handles `INSERT` as well as `UPDATE OF run_status_code`. Every new aggregate must begin as `evaluating`; any other initial status raises `reconciliation_lifecycle_initial_state`. Existing historical rows are not rewritten when migration `0019` installs because the trigger applies only to future row operations. The supported `accept_reconciliation_run()` path already creates `evaluating`, so product behavior is narrowed only for bypass attempts. A real PostgreSQL regression clones valid immutable run scope into a fresh UUID while forcing initial `reconciled` and requires the insert to fail immediately with the initial-state guard.

### Exact replay source-population provenance

The first lifecycle implementation produced a richer first-success document than its exact retry. On first execution, the service appended `statement_population_reference` and `book_population_reference` from the just-reconstructed bridge after loading the transition row. On an idempotent retry, the code returned the stored transition receipt before rebuilding the bridge, but the transition table had never persisted those population identities. The retry was financially non-mutating, yet its response silently dropped the exact source-population provenance that makes a close-evidence receipt independently auditable. Reconstructing a bridge on replay would be worse because a later knowledge state could produce a different population and violate idempotency semantics.

The repair makes the immutable transition command the owner of replay provenance. `reconciliation_run_transition_command` now requires both content-addressed population references; PostgreSQL includes them in the database-computed transition-command hash; the service inserts them from the database-owned bridge observed in the protected snapshot; and `_load_transition_document()` returns the persisted values for both first execution and replay. A focused migration/replay contract intentionally exercises `_load_transition_document()` with persisted population identities and proves an exact retry returns them without calling the close-projection reconstruction. Existing older unit doubles are tolerated only inside the private loader compatibility path; the real schema requires the fields. Exact idempotency therefore means stable effects **and** stable source-provenance receipt shape.

## Deliberate limitation

The database independently enforces aggregate initial state, legal status edges, review/exception eligibility, command immutability, serialization, and persistence of the source-population identities used by the service-derived snapshot. The complete `reconciliation_snapshot_hash` is currently computed by the service from database-owned facts read under the protected snapshot; PostgreSQL binds the digest and the population identities into its own command hash but does not independently rederive every statement/book population identity and monetary bridge component in SQL. Documentation and PR language must preserve this distinction. A later SQL-native derivation would require exact parity/property tests before replacing this boundary.

## Migration sequencing

This lifecycle candidate extends migration `0019_reconciliation_run_command_evidence.sql` only because that migration is still unreleased on the parent dependency-root branch. The child must merge into that parent before the parent reaches protected `develop`. If the parent lands first, the lifecycle schema becomes a forward migration instead; the already-applied `0019` must not be rewritten.

## Next evidence

The stacked PR is not merge-ready until its exact head passes repository validation, real PostgreSQL tests, complete statement/branch and edge-case coverage, SAST/security/dependency review, reproducible package/SBOM/provenance, and required independent review. Real PostgreSQL replay must prove that first execution and exact retry return the same statement/book population identities. The parent dependency root must then rerun its own exact-head evidence after incorporating this child; predecessor checks do not transfer.
