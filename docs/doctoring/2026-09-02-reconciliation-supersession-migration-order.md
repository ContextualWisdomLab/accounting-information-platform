# Doctoring record: reconciliation supersession migration order

**Date:** 2026-09-02  
**Scope:** stacked reconciliation lifecycle candidate in `accounting-information-platform`

## Research question

Can an earlier migration safely harden a PostgreSQL trigger function when a later migration uses `CREATE OR REPLACE FUNCTION` for that same trigger function, and how should repository contracts prevent a later definition from silently restoring an evidence-bypass state transition?

## Finding

No. PostgreSQL 18 specifies that `CREATE OR REPLACE FUNCTION` replaces the current definition of an existing function while preserving the function identity used by dependent triggers. The reconciliation chain defined `accounting_core.reconciliation_match_requires_approval()` in migration `0016_reconciliation_approval_evidence.sql`, then replaced that same function in `0017_reconciliation_approval_lock_order.sql` to add lock-order and snapshot-version behavior. A supersession guard added only to `0016` was therefore absent from the effective fresh-install function after `0017` executed.

The behavioral consequence was authority-significant: raw SQL could insert a new `reconciliation_match` directly as `superseded`, or move a `proposed` match to `superseded`, without first retaining an `approved` or `rejected` reviewed decision. That made `superseded` an alternate terminal state instead of a historical overlay on reviewed evidence.

The repair keeps the existing lock-order/snapshot semantics and repeats the reviewed-predecessor invariant in the latest `0017` definition. The repository contract now examines both migrations that define the trigger function, so a future earlier-only repair fails before PostgreSQL integration. Real PostgreSQL tests remain the authority for the effective installed migration chain.

## RED → GREEN traceability

| Requirement | Evidence |
| --- | --- |
| Fresh installs cannot lose the supersession guard to a later function replacement | `tests/test_reconciliation_supersede_authority.py` requires the reviewed-predecessor invariant in both `0016_reconciliation_approval_evidence.sql` and `0017_reconciliation_approval_lock_order.sql` |
| Direct terminal insertion cannot manufacture historical evidence | `test_direct_superseded_insert_requires_prior_review_evidence` performs a raw PostgreSQL `INSERT ... match_status_code = 'superseded'` and requires SQLSTATE-backed rejection |
| A proposed match cannot skip review | `test_proposed_match_cannot_skip_review_by_becoming_superseded` performs raw PostgreSQL `UPDATE proposed -> superseded` and requires rejection |
| Reviewed decisions remain supersedable | The existing terminal-state branch still permits `approved` or `rejected` to become `superseded` without reopening or changing the immutable decision evidence |
| Lock-order and approval-snapshot controls are preserved | The repair changes only the supersession authority branch inside the latest trigger-function definition; `0017` retains its parent-row/advisory-lock ordering, connected-candidate graph guard, and snapshot-version contracts |

## Authority boundary

This repair grants no posting, reversal, fiscal-period close, chart-account selection, policy-change, or cross-service authority. It removes an evidence-bypass transition from the Reconciliation Review aggregate. Corrections remain append/supersede operations over already reviewed evidence; `superseded` is not a substitute for approval or rejection.

## References (APA 7th)

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE FUNCTION*. https://www.postgresql.org/docs/18/sql-createfunction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
