# Reconciliation supersession authority repair

**Decision date:** 2026-09-02  
**Bounded context:** Reconciliation Review  
**Aggregate root:** `reconciliation_run`

## Observed authority gap

The reconciliation review model treats `approved` and `rejected` as human decisions backed by immutable `reconciliation_approval` evidence. `superseded` is not a third decision: it is a historical overlay applied only after a reviewed decision is retained and a later run/candidate replaces that reviewed evidence.

Review of the still-unreleased `0016_reconciliation_approval_evidence.sql` state machine found that `reconciliation_match_requires_approval()` enforced durable evidence for `approved` and `rejected`, but returned early for every other target status. Consequently, raw SQL could insert a match directly as `superseded`, or update a `proposed` match directly to `superseded`, without first recording an approved/rejected decision snapshot. That path could remove the match from the lifecycle command's `proposed` blocker while retaining no review evidence.

This is an accounting-authority defect rather than a presentation defect: a `reconciled` run is close-review evidence, so terminal review state must not be manufacturable by choosing a status code that bypasses the decision contract.

## Repair

The database trigger now makes supersession a constrained transition rather than a free terminal value:

- a new match cannot be inserted directly as `superseded`;
- a `proposed` match cannot transition directly to `superseded`;
- only a previously `approved` or `rejected` match may transition to `superseded`;
- a no-op update of an already superseded row remains subject to the existing reviewed-terminal immutability rules;
- the existing immutable approval row remains the decision evidence for the superseded historical match.

The repair stays in migration `0016` because that migration is not yet integrated on protected `develop`; no already-applied migration is rewritten. If the dependency root integrates before this child is incorporated, the same invariant must instead be delivered as a new forward migration.

## Verification contract

A dedicated regression module now proves both source contract and real PostgreSQL behavior. The acceptance cases are:

1. raw `INSERT ... match_status_code = 'superseded'` fails closed;
2. raw `UPDATE proposed -> superseded` fails closed;
3. the trigger source retains a named `reconciliation_supersede_requires_reviewed_decision` failure code;
4. existing approved/rejected-to-superseded behavior remains the only supported supersession path.

The tests use the repository's real PostgreSQL foundation fixture. No model output, caller intent, or application-only validation is accepted as a substitute for the database invariant.

## PostgreSQL evidence

PostgreSQL row-level `BEFORE` triggers execute as part of the same transaction as the triggering statement, and an error in the trigger rolls back the statement. PostgreSQL 18 trigger semantics therefore provide the correct enforcement point for a state transition that must remain valid even for privileged direct-SQL application paths. The PostgreSQL project lists 18.6, released 2026-08-13, as the current PostgreSQL 18 maintenance release at the time of this decision.

This record uses PostgreSQL documentation as implementation authority only; it does not claim PostgreSQL, SOC 2, CSAP, or accounting-standard certification.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/current/trigger-definition.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18.6 release notes*. https://www.postgresql.org/docs/release/18.6/
