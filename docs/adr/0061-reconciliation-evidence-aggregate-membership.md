# ADR 0061: Reconciliation evidence has immutable aggregate membership

- Status: Proposed
- Date: 2026-09-01

## Context

ADR 0060 freezes review evidence after a `reconciliation_run` is finalized as `reconciled`. The lifecycle guard installed on reconciliation candidates, matches, statement/journal allocations, approvals, and exceptions originally selected the run to protect from the row's `NEW` tenant/run keys on `UPDATE`. That is insufficient for a mutable evidence row: a privileged SQL caller could rewrite `reconciliation_run_id` so the trigger inspected an unrelated evaluating destination run instead of the reconciled source run. The row would then escape the exact review/exception population bound into the immutable lifecycle snapshot.

This is an aggregate-boundary defect, not merely a trigger implementation detail. Review evidence belongs to the Reconciliation Review aggregate rooted at `reconciliation_run`; moving an existing entity or evidence row between aggregate roots would rewrite the historical meaning of both runs.

## Decision

Tenant and reconciliation-run membership is immutable for every row protected by the reconciliation lifecycle evidence guard. On `UPDATE`, PostgreSQL rejects any change to `tenant_account_id` or `reconciliation_run_id` with `reconciliation_lifecycle_scope_immutable` before it evaluates the destination run state. Corrections or evidence needed by another run are recorded as new rows in that run, with supersession/lineage retained explicitly where the domain supports it.

Migration `0020_reconciliation_evidence_aggregate_membership.sql` replaces the existing `accounting_core.guard_reconciled_run_evidence_mutation()` trigger function while retaining the trigger set, lifecycle advisory-lock key, reconciled/transition freeze checks, and insert/delete behavior established by migration `0019`. A separate migration is used so this causal repair remains append-only and does not collide with concurrent work on the lifecycle migration.

The rule applies uniformly to the trigger-protected reconciliation evidence tables:

- `reconciliation_candidate`
- `reconciliation_match`
- `statement_match_allocation`
- `journal_match_allocation`
- `reconciliation_approval`
- `reconciliation_exception`

Existing table-specific immutability controls remain defense in depth; they do not replace the aggregate-membership invariant.

## DDD mapping

- **Bounded context:** Reconciliation Review.
- **Aggregate root:** `reconciliation_run`.
- **Invariant:** an evidence row is created within exactly one tenant/run aggregate and cannot be re-parented to another aggregate.
- **Correction model:** append/supersede in a new run rather than cross-run row mutation.
- **Domain event impact:** the population represented by `reconciliation_run_reconciled` remains stable after the transition.

## Concurrency and database semantics

PostgreSQL row-level `BEFORE UPDATE` triggers execute once for each row affected and can inspect the current `OLD` and proposed `NEW` row values before the update is applied. Rejecting a changed aggregate key in that trigger is therefore the narrowest authority boundary: it avoids acquiring lifecycle locks for two aggregate roots, avoids introducing a cross-run lock-order problem, and fails before the foreign-key reassignment can rewrite historical evidence. The existing lifecycle advisory lock continues to serialize legal same-aggregate evidence writes with reconciliation completion.

## Verification

Real PostgreSQL acceptance must create a resolved exception on an ordinary run, finalize the run through the supported lifecycle command, create a separate evaluating destination run, and prove that a privileged raw SQL attempt to rewrite the exception's `reconciliation_run_id` fails with the aggregate-membership guard. Existing lifecycle tests continue to prove same-run insertion/update/delete freeze behavior, transition atomicity, idempotent replay, approval completeness, and exact database-owned bridge authority.

## Consequences

The exact reconciliation snapshot cannot be invalidated by moving previously reviewed evidence out of its source aggregate. Downstream period-close evidence can rely on stable run membership rather than treating foreign-key identity as caller-editable metadata. The trade-off is deliberate: data repair that genuinely requires historical reassignment must use an audited migration with explicit provenance rather than ordinary runtime SQL.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
