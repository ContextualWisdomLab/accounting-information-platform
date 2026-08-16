# Product Requirements Document

## Product outcome

Finance teams can receive accounting proposals from CWL products, resolve each proposal through approved accounting policy, post or hold it without duplication, reverse it without destroying history, and prove every trial-balance amount back to source evidence.

## Primary users

- Controllers own accounting policy, chart-account mappings, books, and close controls.
- Accounting operations review held and rejected proposals and perform approved reversals.
- Finance platform engineers operate proposal intake, posting, outbox, reconciliation, and evidence retention.
- Auditors trace a financial statement or trial-balance balance back through journal lines, posting receipts, source proposals, and source payload hashes.

## Core jobs

1. Accept a versioned `accounting_journal_proposal` from an approved source.
2. Detect exact replay versus conflicting reuse of an idempotency key.
3. Resolve tenant, legal entity, accounting book, fiscal period, currencies, account roles, and policy versions.
4. Post a balanced immutable journal or return a structured hold or rejection.
5. Reverse an original journal with an equal-and-opposite journal while preserving both.
6. Produce a trial balance that ties exactly to the included journal population.
7. Return an authoritative `accounting_posting_receipt` to the source system.

## Product principles

- Source systems describe economic events; Accounting determines their book treatment.
- Payment does not automatically equal revenue, and provider payout does not automatically equal cash posting.
- Every monetary result is reproducible from immutable source facts, versions, policy, and mappings.
- Legal books are append-only. Corrections use reversals and replacement entries.
- External provider IDs and bank message fields remain behind adapters.
- Reporting layouts and taxonomies are versioned projections, not fixed columns in the journal core.

## Initial milestone

The first milestone is the proposal-to-trial-balance foundation:

- dependency-free executable reference core;
- PostgreSQL normalized schema;
- proposal, policy, and receipt contracts;
- tenant and period controls;
- exact-decimal, idempotency, reversal, and trial-balance tests;
- architecture, security, operability, and standards traceability.

## Acceptance criteria

- Replaying an identical proposal produces one journal and the original receipt.
- Reusing an idempotency key with a different payload hash fails closed.
- An unbalanced proposal, unknown account role, mismatched tenant, closed period, or unsupported currency treatment produces no journal.
- Reversal retains the original journal and makes the scoped net balance zero for the test fixture.
- Every production statement and branch in the executable reference core and repository tooling is covered.
- All database schemas, tables, columns, policies, and functions follow the repository naming rule.
