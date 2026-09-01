# Doctoring record: reconciliation evidence aggregate membership

**Date:** 2026-09-01  
**Scope:** stacked reconciliation lifecycle candidate in `accounting-information-platform`

## Research question

Can reconciliation evidence that contributed to an immutable `reconciled` lifecycle snapshot be moved to another run by changing its tenant/run foreign keys, and what database control preserves the Reconciliation Review aggregate boundary without adding a cross-run deadlock surface?

## Finding

The existing lifecycle evidence trigger used `NEW.tenant_account_id` and `NEW.reconciliation_run_id` for an `UPDATE`. That correctly protects ordinary same-run mutation but leaves a re-parenting escape: a mutable row can propose a different evaluating destination run, causing the guard to inspect the destination rather than the reconciled source. A resolved `reconciliation_exception` is sufficient to demonstrate the defect because its migration permits state updates and the lifecycle snapshot explicitly binds complete exception state.

PostgreSQL 18 documents that a row-level `BEFORE UPDATE` trigger runs for every affected row before the update is applied. This is the appropriate enforcement point for comparing aggregate identity before and after the proposed update. The repair rejects any change to `tenant_account_id` or `reconciliation_run_id` before acquiring the ordinary single-run lifecycle lock. The guard then keeps its existing same-aggregate serialization and reconciled/transition freeze behavior.

This is preferable to locking both the source and destination runs. Re-parenting is not a legitimate Reconciliation Review operation, so dual-run locking would add a lock-order contract for an operation the domain must reject anyway.

## RED → GREEN traceability

| Requirement | Evidence |
| --- | --- |
| Reconciled evidence cannot escape its source aggregate | `tests/test_reconciliation_lifecycle_aggregate_membership_postgres.py` opens a run, records a resolved exception, reconciles the run, creates a second evaluating run, and attempts raw SQL reassignment |
| Aggregate identity is database-owned | `0020_reconciliation_evidence_aggregate_membership.sql` compares `OLD` and `NEW` tenant/run keys in the existing lifecycle trigger function and raises `reconciliation_lifecycle_scope_immutable` |
| Same-run lifecycle concurrency remains unchanged | The replacement function retains `acquire_reconciliation_run_lifecycle_lock()` and the existing reconciled/transition checks |
| No cross-run lock-order heuristic is introduced | Illegal re-parenting fails before any destination lifecycle lock is acquired |
| DDD semantics remain explicit | ADR 0061 defines immutable evidence-to-`reconciliation_run` membership and append/supersede correction semantics |

## Authority boundary

This repair does not give reconciliation posting, reversal, chart-account selection, fiscal-period close, policy-change, or cross-service authority. It narrows database mutation authority so the exact evidence population behind a lifecycle receipt cannot be rewritten by foreign-key reassignment. Any exceptional historical repair must be an audited migration with retained provenance, not a normal product operation.

## References (APA 7th)

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
