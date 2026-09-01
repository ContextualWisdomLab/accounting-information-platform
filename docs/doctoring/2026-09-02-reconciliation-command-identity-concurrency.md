# Doctoring record: reconciliation command identity concurrency

**Date:** 2026-09-02  
**Scope:** reconciliation run-opening and lifecycle-command idempotency on the stacked lifecycle candidate

## Control question

Can one tenant-scoped reconciliation idempotency key become two different durable command identities when a run-opening command and a lifecycle-reconciliation command execute concurrently?

The previous design could. `reconciliation_run_command` and `reconciliation_run_transition_command` each had their own unique key and the application used different advisory-lock names. Two transactions could therefore pass cross-table existence checks before either command became visible and then commit different command families under the same tenant/key.

## Database-owned repair

Unreleased migration `0019_reconciliation_run_command_evidence.sql` now owns the namespace with `accounting_core.reconciliation_command_identity`.

- The primary key is `(tenant_account_id, reconciliation_command_identity_key)`. It is the one physical uniqueness boundary shared by run-opening and run-reconciliation commands.
- `command_family_code` is constrained to `run_opening` or `run_reconciliation`; the registry records identity ownership, not journal, ledger, bank-balance, or close facts.
- BEFORE INSERT triggers on both command tables reserve the tenant/key through `reserve_reconciliation_command_identity()` inside the same transaction as the command evidence.
- A duplicate reservation raises SQLSTATE `23505` with `reconciliation_command_identity_conflict`. The command-table insert cannot commit without the registry reservation, and a later failure rolls the reservation back with the transaction.
- The registry is immutable, tenant-scoped through forced RLS, and unavailable to `PUBLIC`.

PostgreSQL 18 documents that uniqueness constraints are enforced by unique indexes and that unique-index conflict checking is integrated with insertion so races are not reduced to a separate preflight lookup. PostgreSQL also documents that INSERTs against unique indexes can block on concurrent transactions modifying the same indexed values. That behavior is the concurrency primitive used here: application advisory locks and prior SELECTs may improve diagnostics, but they are not the authority for cross-command uniqueness.

## Falsifiable evidence

`tests/test_reconciliation_cross_command_identity_postgres.py` exercises the boundary on real PostgreSQL.

1. It opens a normal reconciliation run, then attempts to insert lifecycle-transition evidence with that run-opening key. PostgreSQL must reject the second command family at the shared identity boundary.
2. It starts two transactions against the same tenant/key and different command-family codes from a barrier. Exactly one registry row may commit; the other transaction must fail with SQLSTATE `23505`. The assertion does not depend on which writer wins.

The repository-wide exact-head Foundation job remains the acceptance authority for this test. Queued, skipped, cancelled, stale, predecessor-head, or model-only evidence is non-passing.

## DDD and integration boundary

`reconciliation_command_identity` is an internal Reconciliation bounded-context control relation. It must not be exported as a second financial ledger or copied into Context Graph Contracts or Enterprise Architecture Core as financial truth. External billing, ERP, bank, identity, Context Graph, and EA systems continue to interact through released/versioned API, event, or anti-corruption contracts; this repair introduces no cross-service SQL.

The registry does not grant posting, reversal, fiscal-period close, accounting-policy, or reconciliation-approval authority. It only prevents ambiguous command identity.

## Residual application concern

Sequential lifecycle reuse of an opening key is already translated to `IdempotencyConflictError` before the transition insert. A race is still ultimately decided by PostgreSQL, so API/worker boundaries must preserve the database conflict as a stable idempotency-domain outcome rather than expose provider-specific exception text. That normalization is separate from the database invariant and must not weaken or replace it.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: INSERT*. https://www.postgresql.org/docs/18/sql-insert.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Index uniqueness checks*. https://www.postgresql.org/docs/18/index-unique-checks.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Unique indexes*. https://www.postgresql.org/docs/18/indexes-unique.html
