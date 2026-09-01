# ADR 0059: Evidence-backed reconciliation completion command

- **Status:** Proposed
- **Date:** 2026-09-01
- **Decision owner:** Accounting Record & Close bounded context
- **Depends on:** ADR 0054, ADR 0055, ADR 0056, ADR 0058 and the database-owned close-projection repair in PR #29

## Context

A reconciliation run is created as `evaluating`, while authority-bearing close-package construction correctly requires the locked run to be `reconciled`. Direct SQL status mutation is not a commercial owner-control path: it does not bind who completed the review, why they were authorized to do so, which immutable statement/book populations were evaluated, which approved match population was current, whether exceptions were resolved, or which exact book-to-bank bridge was accepted.

A generic `PATCH run_status_code` endpoint would also make an internal state representation part of the buyer contract and create an unsafe promotion primitive. The domain needs one named business command whose only authority is to complete reconciliation review from database-owned evidence.

## Decision

Introduce the **Reconciliation Completion** command in the Accounting Record & Close core subdomain.

The `reconciliation_run` aggregate remains the minimal transaction boundary for the lifecycle state. `reconciliation_completion_command` is immutable command/audit evidence attached to that aggregate; it is not a second mutable aggregate. Bank Statement Evidence and Reconciliation Review remain supporting contexts. Journal Posting and Fiscal Period Close remain separate authority boundaries: successful reconciliation completion cannot post/reverse a journal or close/reopen a period.

The command accepts only:

- the bound tenant reference;
- one persisted `reconciliation_run_id`;
- a tenant-scoped `reconciliation_completion_key` for idempotency;
- the accountable `actor_reference`; and
- the purpose code `reconciliation_close_review`.

The caller does **not** supply population identities, approval digests, bridge values, or a target status. In one PostgreSQL `REPEATABLE READ` transaction the owner implementation locks the run, verifies that it is `evaluating` or `review_required`, rejects open reconciliation exceptions and unreviewed `proposed` matches, reconstructs the database-owned statement/book populations and exact bridge through the same domain service used by authority-bearing close packages, reads the current approved match/approval snapshot population, and derives deterministic SHA-256 identities for that evidence.

The immutable command stores `statement_population_hash`, `book_population_hash`, `approval_population_hash`, and `bridge_evidence_hash` together with the purpose, actor, idempotency key, and complete command hash. The command is inserted before the run status changes. A database trigger rejects the first transition to `reconciled` unless the old state is `evaluating` or `review_required` and matching completion-command evidence exists in the same tenant/run transaction. The trigger independently rejects open exceptions and pending proposed matches. The completion command itself is guarded by the same state/exception/pending-match preconditions, forced RLS, uniqueness, and an immutability trigger.

The same transaction appends an `accounting_integration.outbox_event` with event type `reconciliation_run.reconciled`. An exact retry of the same idempotency key replays the immutable command without reinterpreting later mutable state; a changed command under the same key fails with `IdempotencyConflictError`, and the unique tenant/run command prevents a second key from replacing the completion that already reconciled the run.

## State-machine boundary

```mermaid
stateDiagram-v2
    [*] --> evaluating: open reconciliation run
    evaluating --> review_required: review policy / exception workflow
    evaluating --> reconciled: evidence-backed completion command
    review_required --> reconciled: evidence-backed completion command
    evaluating --> not_reconciled: explicit non-reconciliation decision
    review_required --> not_reconciled: explicit non-reconciliation decision
    reconciled --> superseded: later explicitly governed successor evidence
```

This ADR governs only the two arrows into `reconciled`. It does not invent policy for the other arrows; those require their own owner commands/evidence before becoming public mutation APIs.

## Invariants

1. A first transition to `reconciled` has exactly one immutable tenant/run completion command.
2. The command cannot be inserted for a run outside `evaluating` or `review_required`.
3. Open exceptions and `proposed` matches block both command insertion and the DB status transition.
4. Statement/book population identity, approved population identity, and exact bridge evidence are database-derived in one consistent snapshot; no caller value can substitute them.
5. The book-to-bank bridge must have zero unexplained difference under exact Decimal arithmetic before the completion command can be recorded.
6. Exact idempotent retry replays immutable evidence. Changed command identity under the same key fails closed.
7. Completion, status transition, and transactional-outbox evidence commit atomically.
8. Reconciliation completion does not grant journal posting, reversal, fiscal-period close, tax filing, or accounting-policy authority.
9. `actor_reference` and `completion_purpose_code` are retained as audit facts. A later purpose-bound Keyverse/PDP integration may strengthen admission, but must not reinterpret or overwrite this evidence.

## API layering

The Python domain API is `accept_reconciliation_completion`. The buyer-facing stdlib HTTP transport is deliberately implemented as a stacked successor so the database/domain authority change and transport routing have independently reviewable diffs. The HTTP contract will expose `POST /reconciliation-completions`; it must not expose a generic run-status mutation endpoint.

## Consequences

The product gains a lawful path from normal API-created runs to the state required by close-package construction. Controllers can distinguish 'review completed from authoritative evidence' from an internal status write, and downstream audit/event consumers receive an atomic transition event. The cost is an additional durable command table and one evidence-hashing read path, accepted because correctness and auditability dominate latency for this control action.

## Research and standards traceability

- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html
- Cai, Z., Liu, S., Wei, H., Chen, Y., & Pan, A. (2025). Fast verification of strong database isolation (Extended Version). *Proceedings of the VLDB Endowment, 19*, 563–575. https://consensus.app/papers/fast-verification-of-strong-database-isolation-extended-cai-liu/e7131ce449515d41ab6104cc32c3e2b7/
- World Wide Web Consortium. (2013). *PROV-O: The PROV ontology*. https://www.w3.org/TR/prov-o/

The first two sources support making isolation semantics an explicit, verifiable integrity contract; PROV-O supports retaining provenance as separately identifiable evidence. None of these sources grants accounting authority or substitutes for the repository's exact PostgreSQL integration tests.
