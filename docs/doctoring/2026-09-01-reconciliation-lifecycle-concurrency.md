# Doctoring record: reconciliation lifecycle concurrency and status authority

**Date:** 2026-09-01  
**Scope:** stacked reconciliation lifecycle candidate on `accounting-information-platform`

## Research question

What concurrency and transaction contract is required so a reconciliation run can move from `evaluating`/`review_required` to `reconciled` without blessing stale, partially reviewed, or caller-shaped accounting evidence?

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
| Legal state edge | `reconciliation_run` row lock + migration status guard permits only `evaluating`/`review_required` → `reconciled` | Raw SQL update without transition command must fail |
| Durable command identity | `reconciliation_run_transition_command` stores tenant/run/idempotency/snapshot/actor/purpose/effective time and database-owned command hash | Exact replay returns same receipt; changed key evidence conflicts |
| Review completeness | Service and database transition trigger reject proposed matches and approved/rejected matches without decision-consistent approval snapshot | Unit cases and migration trigger contract |
| Exception completeness | Service and DB trigger reject `resolution_status_code='open'` | Unit + PostgreSQL evidence-freeze path |
| Exact monetary authority | Service invokes `_database_owned_close_projection_evidence()` inside the lifecycle transaction and hashes its source-population identities and exact bridge fields | Existing real PostgreSQL population/bridge tests plus lifecycle bridge-failure regression |
| Currency authority | The locked `reconciliation_run` row supplies `currency_code` to the transition snapshot digest; the close-projection helper is not treated as the owner of run scope | Regression constructs a bridge object with no currency attribute and still hashes successfully when the locked run currency is supplied |
| Post-transition immutability | Candidate/match/allocation/approval/exception trigger paths acquire the same run lock and reject writes when run is reconciled | PostgreSQL late-exception insert must fail after supported transition |
| Atomic publication evidence | Transition command, status update, and `reconciliation_run_reconciled` outbox row share one transaction | PostgreSQL test reads all three after command completion |

## Current-head causal repair

The initial lifecycle implementation computed `reconciliation_snapshot_hash` with `bridge.currency_code`. The dependency-root `_DatabaseOwnedCloseProjectionEvidence` deliberately owns statement/book populations and exact bridge arithmetic but does not expose `currency_code`; currency is already immutable reconciliation-run scope. A real lifecycle execution could therefore reach a fully tied bridge and then fail with an `AttributeError` while building the transition digest, even though unit doubles happened to carry an extra `currency_code` attribute.

The repair follows the existing authority model instead of widening the bridge helper: the already locked `reconciliation_run` query now returns `currency_code`, and the transition digest receives that database-owned run-scope value explicitly. A focused regression intentionally omits currency from the bridge object so future refactors cannot silently reintroduce the projection-shape dependency. The compatibility fallback inside the private digest helper exists only for existing direct unit callers; the production lifecycle path passes the locked run currency explicitly.

## Deliberate limitation

The database independently enforces legal status edges, review/exception eligibility, command immutability, and serialization. The complete `reconciliation_snapshot_hash` is currently computed by the service from database-owned facts read under the protected snapshot; PostgreSQL binds the digest into its own command hash but does not independently rederive every statement/book population identity and monetary bridge component in SQL. Documentation and PR language must preserve this distinction. A later SQL-native derivation would require exact parity/property tests before replacing this boundary.

## Migration sequencing

This lifecycle candidate extends migration `0019_reconciliation_run_command_evidence.sql` only because that migration is still unreleased on the parent dependency-root branch. The child must merge into that parent before the parent reaches protected `develop`. If the parent lands first, the lifecycle schema becomes a forward migration instead; the already-applied `0019` must not be rewritten.

## Next evidence

The stacked PR is not merge-ready until its exact head passes repository validation, real PostgreSQL tests, complete statement/branch and edge-case coverage, SAST/security/dependency review, reproducible package/SBOM/provenance, and required independent review. The parent dependency root must then rerun its own exact-head evidence after incorporating this child; predecessor checks do not transfer.
