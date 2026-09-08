# ADR 0060: Evidence-derived reconciliation-run lifecycle authority

- Status: Proposed
- Date: 2026-09-01
- Last verified against code: 2026-09-08

## Context

`accept_reconciliation_run()` opens a tenant-scoped reconciliation aggregate in `evaluating`. A later `reconciled` state is authority-bearing accounting-control evidence: it can be attached to period-close review, so a direct status rewrite, caller-selected population digest, or publication record that is not bound to the lifecycle command would create evidence the ledger cannot reproduce or audit.

The lifecycle transition belongs to the **Reconciliation Review** supporting subdomain. It does not post or reverse journals, select chart accounts, change accounting policy, or close a fiscal period. A reconciled run remains evidence for a separately authorized period-close command.

Three database facts must agree for one reconciliation completion:

1. one immutable `reconciliation_run_transition_command`;
2. the same run in `reconciled`; and
3. one immutable `reconciliation_run_reconciled` outbox event bound to that transition command and its database-assigned hash.

The transition digest also commits database-owned statement/book populations and monetary bridge evidence. Those digests must be independent of the PostgreSQL session `TimeZone`; otherwise two sessions observing the same accounting instants can produce different authority hashes.

## Decision

The `reconciliation_run` aggregate is created only in `evaluating` and owns the supported transition from `evaluating` or `review_required` to `reconciled`. The supported application command is tenant-scoped and idempotent. Its immutable transition evidence includes run identity, target status, database-derived reconciliation snapshot hash, statement-population reference, book-population reference, actor reference, purpose code, effective time, and the database-assigned transition-command hash.

No changed `run_status_code` is a generic field edit. PostgreSQL rejects an initial state other than `evaluating`. The only named status-changing command introduced by this slice is reconciliation. Other changed targets remain fail-closed until their own command evidence, legal predecessor states, authorization, idempotency, audit, and publication contracts exist.

Review evidence has immutable aggregate membership. Candidate, match, statement allocation, journal allocation, approval, and exception rows cannot move between tenant/run aggregates. Corrections are new or superseding evidence, not reassignment behind an already reviewed aggregate.

### Database-owned snapshot authority

Migration `0021_reconciliation_run_database_snapshot_authority.sql` recomputes transition authority from PostgreSQL facts immediately before the transition row is stored. It resolves the retained statement scope, validates exactly one opening and closing balance, reconstructs immutable statement entries, reconstructs assigned cash-account journals, checks currency scope, checks statement arithmetic, recomputes statement and journal allocation capacities, recomputes outstanding bank/book amounts, rejects an unexplained book-to-bank bridge, and includes reviewed-match and exception populations in the final snapshot.

The parent `accounting_reconciliation_transition_database_authority_guard` invokes `reconciliation_run_database_snapshot_authority` before the child `accounting_reconciliation_transition_evidence_snapshot_guard` composes maker-checker evidence. This ordering preserves the database-derived reconciliation snapshot and its statement/book population identities before the final transition hash is assigned.

The database overwrites caller-supplied `reconciliation_snapshot_hash`, `statement_population_reference`, and `book_population_reference` before the transition-command hash is assigned. Application calculations remain useful for buyer-facing diagnostics, but they are not the final transition authority.

Every `timestamptz` value that participates in the hashed database JSON is serialized explicitly in UTC before hashing. The current hashed timestamp population is:

- `general_journal.posted_at` in `book_population`;
- `reconciliation_exception.effective_at` in `exception_population`; and
- `reconciliation_run.knowledge_cutoff_at` in the final authority snapshot.

The canonical representation is `YYYY-MM-DD"T"HH24:MI:SS.US"Z"` after `AT TIME ZONE 'UTC'`. This changes serialization only. Cutoff predicates, effective/system-time meaning, population membership, ordering, and exact Decimal/numeric arithmetic are unchanged.

### Command/status/outbox atomicity

The application continues to create the outbox row explicitly. PostgreSQL does not synthesize the event. Instead, deferred constraint triggers validate the completed transaction at commit:

- every transition command must have exactly one matching `reconciliation_run_reconciled` event;
- every such lifecycle event must bind exactly one transition command;
- tenant identity must match;
- `aggregate_reference` must equal `urn:cwl:accounting:reconciliation_run:{run_id}`;
- `payload_reference` must equal `urn:cwl:accounting:reconciliation_run_transition:{transition_id}`; and
- `payload_hash` must equal the database-assigned transition-command hash.

A partial unique index prevents duplicate lifecycle events for the same tenant/transition payload reference. Lifecycle outbox identity, event type, aggregate reference, payload reference, payload hash, and `created_at` are immutable after insertion. `published_at` remains mutable so the existing outbox publisher can acknowledge successful publication without rewriting accounting evidence. Unrelated outbox event types keep their existing semantics.

Together with the existing deferred command/status pair guard, a transaction that writes a lawful transition command and changes the run to `reconciled` but omits the lifecycle outbox event cannot commit. A forged or mismatched lifecycle event also cannot commit. Rollback therefore leaves no transition command, no reconciled state, and no lifecycle publication evidence.

### Concurrency and replay

The application admits the lifecycle advisory lock before opening the authority-bearing `REPEATABLE READ` snapshot. Evidence writers use the same run lifecycle transaction lock. Transition insertion independently rejects proposed matches, open exceptions, and terminal reviewed matches without current decision-consistent approval evidence.

Exact idempotent replay reads persisted transition evidence and returns the same source-population references and transition hash. It does not recompute authority from later statement, journal, match, or exception state and does not append another lifecycle event.

## Alternatives considered

### Trust the application snapshot only

Rejected. A privileged SQL writer could otherwise store command/status evidence whose monetary population was never derived by the database authority function.

### Generate the outbox event in a database trigger

Rejected. Publication remains an explicit application transaction responsibility. Automatically creating the event would hide a missing application-side write and blur responsibility between domain command handling and database invariant enforcement.

### Validate lifecycle publication immediately after each statement

Rejected. The supported transaction writes command, status, and event as separate SQL statements. PostgreSQL deferred constraint triggers allow the invariant to be checked against the completed transaction without requiring unsafe statement ordering or temporary invalid bypasses.

### Hash raw `timestamptz` JSON values

Rejected. PostgreSQL stores timezone-aware timestamps internally in UTC but renders them according to the session `TimeZone`. Hashing that rendered representation can make identical accounting instants produce different digests. The hashed representation is therefore normalized explicitly to UTC.

### Make all outbox rows globally immutable

Rejected in this slice. The accounting platform already has generic outbox publication behavior. The lifecycle control adds immutability only to `reconciliation_run_reconciled` evidence and deliberately preserves `published_at` updates.

## Database authority and concurrency invariants

1. A new `reconciliation_run` begins in `evaluating`.
2. `reconciliation_run_transition_command` is tenant-scoped, forced-RLS, immutable evidence with at most one reconciled transition per run.
3. PostgreSQL rederives statement/book populations and the exact book-to-bank snapshot, then overwrites caller snapshot/population digests.
4. The database assigns the transition-command hash from canonical command evidence after the database-owned snapshot fields are assigned.
5. A changed run status reaches `reconciled` only with exactly one transition command in the same transaction.
6. Exactly one matching lifecycle outbox event must exist in the same committed transaction, and every lifecycle event must map back to one exact transition command.
7. Lifecycle event identity/hash/time evidence is immutable; only `published_at` may change.
8. Candidate, match, allocation, approval, and exception membership is immutable and freezes after transition authority exists.
9. Hashed timezone-aware instants are serialized in explicit UTC, making the authority digest independent of session `TimeZone`.
10. Exact replay uses persisted transition evidence and does not create duplicate lifecycle publication evidence.

## DDD mapping

- **Subdomain:** Reconciliation Review (supporting).
- **Bounded context:** Bank Reconciliation / Evidence-Audit integration boundary.
- **Aggregate root:** `reconciliation_run`.
- **Command evidence entity:** `reconciliation_run_transition_command`.
- **Value evidence:** lifecycle idempotency key, target status, database snapshot hash, statement/book population references, actor, purpose, effective time, command hash.
- **Domain event:** `reconciliation_run_reconciled` through `accounting_integration.outbox_event`.
- **Domain service:** `reconcile_reconciliation_run()` performs the supported transition transaction after database-owned review/bridge checks.
- **Repository/database authority:** PostgreSQL owns retained accounting facts, transition snapshot derivation, command/status pairing, lifecycle-event binding, immutability, tenant isolation, and commit-time cardinality.
- **Invariant:** `reconciled` means one reviewed run whose database-owned bridge ties exactly and whose command, status, and publication evidence are one durable fact.
- **Anti-corruption boundary:** bank/billing/payment evidence remains foreign input; no external context can write accounting truth through cross-service SQL.

The reconciliation aggregate remains separate from Period Close. This avoids turning ingestion, matching, review, journal posting, evidence publication, and close approval into one transaction boundary.

## Consequences

Controllers gain a repository-owned path from run evaluation to review-complete reconciliation whose retained evidence can be reproduced without trusting caller-selected digests. Privileged raw SQL cannot manufacture a reconciled run by supplying only command/status rows or by forging an outbox event. Cross-session TimeZone configuration cannot change the hash of the same retained accounting instants.

The new deferred event checks add commit-time reads on transition completion only. They do not change normal journal posting, generic outbox insertion, or outbox publication paths. The partial unique index is scoped to `reconciliation_run_reconciled` events.

The public surface introduced here remains the package API. A buyer-facing HTTP lifecycle route requires purpose-bound authorization before it can become a supported high-impact control path.

This slice still does not introduce a dedicated PostgreSQL capability role solely for reconciliation completion. Deployment least-privilege credentials remain a separate operability/security hardening lane.

## Verification

Acceptance evidence must bind to one unchanged exact head and include:

- unit tests for validation, replay/conflict, legal states, review completeness, missing currency/provenance, command-identity normalization, deterministic snapshot binding, and lock ordering;
- real PostgreSQL tests proving raw terminal insertion/status rewrite fail without command authority;
- real PostgreSQL tests proving a command + reconciled status cannot commit without the exact lifecycle outbox event, a forged event cannot satisfy the binding, rollback leaves zero lifecycle artifacts, the exact event commits, publication can update `published_at`, and lifecycle identity/hash evidence cannot be updated or deleted;
- a real PostgreSQL cross-TimeZone test evaluating the same retained facts under `UTC` and `Asia/Seoul` and requiring identical database snapshot, statement-population, and book-population hashes;
- real PostgreSQL close-projection tests proving statement/book populations, allocation capacities, and exact bridge values are database-derived under the assigned accounting-book scope;
- exact 100% owned production statement/branch coverage, public-docstring/repository contracts, compile/import checks, and reproducible package/SBOM/provenance; and
- current-head SAST, security/dependency, central required workflows, resolved review threads, and independent current-head review.

## Research basis

PostgreSQL 18 stores timezone-aware timestamps internally in UTC and converts their rendered value using the session `TimeZone`. Explicit UTC formatting is therefore required where rendered timestamp text participates in an authority hash. PostgreSQL constraint triggers can be declared `DEFERRABLE INITIALLY DEFERRED`, allowing command/status/outbox consistency to be checked against the complete transaction at commit. Row-level trigger functions remain appropriate for immutable evidence guards and publication-only update rules.

### References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Date/time types*. https://www.postgresql.org/docs/18/datatype-datetime.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Data type formatting functions*. https://www.postgresql.org/docs/18/functions-formatting.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html
