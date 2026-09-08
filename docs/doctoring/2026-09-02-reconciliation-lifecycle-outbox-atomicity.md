# Reconciliation lifecycle command/status/outbox atomicity

Date: 2026-09-02

## Problem

The supported reconciliation lifecycle command already writes the immutable transition command, updates the run to `reconciled`, and appends the `reconciliation_run_reconciled` accounting outbox event in one application transaction. The existing deferred PostgreSQL lifecycle guard, however, proved only command/status consistency. A privileged or direct SQL writer could therefore insert a syntactically valid transition command and change the run status while omitting the corresponding publication receipt. That would leave durable reconciliation authority without the exact transactional event evidence required by ADR 0060.

## Constraints

- PostgreSQL remains the final lifecycle-row authority; application checks are defense in depth.
- The repair must not recreate the parent statement/book bridge, accept caller-selected population hashes, or weaken the database-owned reconciliation snapshot introduced by the lifecycle parent.
- The repair must preserve the child maker-checker exception-resolution boundary and its own command/status/outbox invariant.
- The event match must bind one tenant, one reconciliation run, the exact immutable transition-command identity, and the database-assigned transition command hash.
- Duplicate matching publication evidence is not acceptable evidence of one lifecycle command.
- No new authority is granted to post or reverse journals, open or close fiscal periods, submit tax evidence, mutate accounting policy, or write foreign commercial truth.

## RED evidence

`tests/test_reconciliation_lifecycle_outbox_pair_postgres.py` was committed before the production repair. It opens a real PostgreSQL transaction, inserts lifecycle transition evidence and changes the authoritative run status to `reconciled` while intentionally omitting the accounting outbox row. `SET CONSTRAINTS ALL IMMEDIATE` must fail with `reconciliation_lifecycle_atomic_outbox`; after rollback, the run remains `evaluating`, no transition command remains, and no matching lifecycle outbox event exists.

This is stronger than an application mock because it exercises the commit-time invariant against the installed PostgreSQL schema and a privileged/direct SQL writer.

## Decision

Migration `0021_reconciliation_exception_resolution_outbox_pair.sql` now adds deferred constraint trigger `reconciliation_run_transition_outbox_pair_guard` backed by `accounting_core.enforce_reconciliation_run_transition_outbox_pair()`.

For every inserted `accounting_core.reconciliation_run_transition_command`, commit is accepted only when exactly one `accounting_integration.outbox_event` exists with all of the following values:

- the same `tenant_account_id`;
- `event_type_code = 'reconciliation_run_reconciled'`;
- `aggregate_reference = 'urn:cwl:accounting:reconciliation_run:' || reconciliation_run_id`;
- `payload_reference = 'urn:cwl:accounting:reconciliation_run_transition:' || reconciliation_run_transition_command_id`;
- `payload_hash = reconciliation_transition_command_hash`.

Zero or multiple exact matches fail with SQLSTATE `23514` and the stable diagnostic token `reconciliation_lifecycle_atomic_outbox`.

The invariant is `DEFERRABLE INITIALLY DEFERRED` because the supported application transaction writes the transition command before the outbox row. It is evaluated at the transaction boundary, after the parent database-authority and command-hash triggers have assigned the final PostgreSQL-owned transition evidence.

## Alternatives rejected

1. **Application-only atomicity.** Rejected because direct SQL, migration, break-glass, or compromised privileged writers would still be able to persist authority without publication evidence.
2. **A non-deferred row trigger on the transition command.** Rejected because the supported transaction intentionally inserts the command before the outbox event; immediate validation would reject the correct write order or force a second competing command shape.
3. **Checking only event type and run reference.** Rejected because an unrelated or stale lifecycle event could satisfy the guard. The exact transition-command payload reference and database-assigned command hash are material identity.
4. **At-least-one matching event.** Rejected because duplicate event evidence would make command-to-publication cardinality ambiguous. The durable contract is exactly one matching event.
5. **Reimplementing the book-to-bank bridge in this trigger.** Rejected because bridge and population ownership already belongs to the parent lifecycle database-authority function. Duplicating it would create a second accounting truth boundary and drift risk.

## Failure and operations scenarios

- If application code omits the outbox insert, commit fails and the command/status mutation rolls back.
- If a direct SQL writer inserts a mismatched event, commit fails because tenant, aggregate, payload identity and payload hash must all match.
- If the same command acquires duplicate matching outbox rows, commit fails because cardinality must be exactly one.
- A publisher may later mark the retained outbox row as published; publication delivery state is separate from the commit-time accounting evidence that the event was durably recorded with the command.
- Operators investigating `reconciliation_lifecycle_atomic_outbox` should repair the writer transaction rather than disable the constraint or synthesize historical evidence.

## Verification status

The RED PostgreSQL regression precedes the production repair. The source repair is present on the active maker-checker writer branch, but the current exact-head Accounting Foundation CI remains required before integration; queued, pending, predecessor-head, skipped, model-only, or prose evidence is non-passing. After this documentation commit changes the branch head, all exact-head checks must be reacquired on the successor commit before any merge claim.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET CONSTRAINTS*. https://www.postgresql.org/docs/18/sql-set-constraints.html
