# ADR 0069: Explicit aggregate allocation source provenance

- Status: Proposed
- Date: 2026-09-22

## Problem

`aggregate_allocations()` returns reviewable Bank Reconciliation allocation evidence. The first repair in this decision removed manufactured `journal-aggregate` and `KRW` defaults, but callers could still supply `journal_total`, `journal_reference`, and `currency_code` as three independent scalars. A caller could therefore create a mutually consistent book-side story without presenting the repository-owned posted-journal evidence from which that identity, amount, and currency were admitted.

Exact monetary conservation does not prove provenance. A valid aggregate proposal must bind its journal identity, book-side amount, and currency to one admitted journal evidence object before statement-side allocation rows become reviewable evidence.

## Constraints

- Bank Reconciliation remains proposal/evidence authority only. This decision grants no journal posting, reversal, approval, period-close, account-selection, or accounting-policy authority.
- `ReconciliationAllocation` remains exact-`Decimal`, tenant/run/source scoped and immutable.
- `BookJournalEvidence` remains read-only posted-journal evidence eligible for deterministic reconciliation; accepting that value object does not by itself prove a fresh PostgreSQL snapshot or grant persistence authority.
- The aggregate statement population remains ADR 0054's exact built-in tuple of exact built-in `(statement_entry_reference, Decimal amount)` pairs; this decision does not redesign that contract.
- Currency syntax, exact Decimal admission, source identity, CRDT/DBIT direction, accounting date, and optional reference validation remain owned by `BookJournalEvidence` and its reconciliation-domain validators.
- The package is typed (`py.typed`); runtime compatibility sentinels must not be advertised as accepted public provenance types.
- Split planning already requires exact repository-owned `BookJournalEvidence`; aggregate planning should not use a weaker journal-side provenance boundary.

## Alternatives considered

### Keep synthetic `journal-aggregate` and `KRW` defaults

Rejected. These values are convenience placeholders, not source evidence. Their presence makes a successfully conserved allocation look more authoritative than the caller proved.

### Require explicit `journal_total`, `journal_reference`, and `currency_code` scalars

Rejected as the final provenance boundary. Explicit scalars are better than synthetic defaults, but they still allow callers to assemble identity, money, and currency independently without showing that one admitted journal source owns all three values.

### Accept a journal-shaped protocol or duck-typed object

Rejected. Allocation planning would execute caller-defined attribute behavior and bypass the `BookJournalEvidence` constructor's exact identity, monetary, currency, direction, and date admission rules.

### Require exact `BookJournalEvidence` and derive the aggregate book side from it

Selected. Typed callers provide one exact repository-owned `BookJournalEvidence`. Aggregate planning reads journal identity, amount, and currency only after exact-type admission, then compares the immutable statement population against that admitted book amount. The old scalar keyword names remain runtime-only compatibility sentinels so legacy calls fail through a repository-owned `ValueError` rather than silently becoming accepted evidence.

## Decision

`aggregate_allocations()` must consume one exact `BookJournalEvidence` as the journal-side source for a successful plan.

- Public overloads require `journal_evidence: BookJournalEvidence`; `journal_total`, `journal_reference`, and `currency_code` are not part of the accepted typed contract.
- Runtime admission checks `type(journal_evidence) is BookJournalEvidence` before reading any journal attributes, preventing duck-typed or subclass-defined behavior from entering allocation planning.
- Journal identity, book-side amount, and currency are derived atomically from `journal_evidence`.
- Legacy scalar provenance keywords are rejected even when their values are mutually consistent.
- Statement-population shape, distinct-statement identity, exact Decimal conservation, tenant/run scope, and immutable allocation output remain unchanged.
- No schema, migration, approval, Posting, Period Close, Accounting Policy, Billing truth, or LLM authority changes are introduced.

## Evidence

The first RED in this ADR lineage is `dea57479af8a9fa5f17d9adaa4c1aec095711433` on parent #158 exact `530f179b6e3493916bfe920285caf3d040b7f3f8`. It demonstrated that aggregate planning manufactured journal identity/currency when they were omitted. Runtime repair `b49dc34c81c4e9f4c7232bd5e3ea480980fa6861` removed those synthetic defaults. Current-exact review then found that `str | None` leaked into the typed API; RED `723a05e729224b58add1e471d67b55f87e9bdcbe` and repair `feeffa7b2e0ef7131071d92cca49b9cfe51ee7ac` narrowed the typed contract to required strings.

The next control finding is that three explicit scalars still do not prove one admitted journal source. RED `30d93402c2312fa0a8e4c0c3e3ce1f34afa48f9a` keeps one exact `1000.00` statement item, tenant/run scope, and conservation target fixed while varying only book-side provenance. Parent #159 exact `9eaf743aae6ac510f87fd4d13b7998f7374142b0` accepts scalar-only provenance and has no `journal_evidence` API. The RED requires exact `BookJournalEvidence`, rejects scalar-only, duck-typed, and subclass sources, and requires typed overloads to publish only the evidence-object contract.

Causal production repair `2e2f65c40c35b4bda1de8f86c144fd14c7c9df7e` adds exact `BookJournalEvidence` admission before attribute reads, derives journal identity/amount/currency from that value object, and keeps old scalar keyword names only as runtime rejection sentinels. Ordinary consumer-fixture descendants then adapt conservation, statement-population, Decimal, currency, and predecessor provenance tests to the same journal-evidence source without changing their statement-side accounting assertions.

## Risks and effects

This intentionally narrows a public Python call shape. Callers that previously supplied journal total, identity, and currency independently must construct or obtain admitted `BookJournalEvidence` and pass that single value object instead. The compatibility cost is deliberate: exact conservation should not make caller-assembled source provenance look equivalent to repository-admitted journal evidence.

This still does not prove that a supplied `BookJournalEvidence` came from the current PostgreSQL-owned snapshot. Persistence, approval, lifecycle, and close-package owners remain responsible for binding reviewed allocation evidence to authoritative database state, lock ordering, tenant scope, and recovery controls. This ADR only closes the in-memory proposal boundary that previously accepted weaker journal-side provenance than split planning.

## Follow-up

PR #37 remains the canonical single writer for shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md`. After this decision reaches protected integration through the Bank Reconciliation owner path, #37 must rebuild those records from the exact protected tree and record the progression from synthetic aggregate provenance to explicit scalars and finally exact `BookJournalEvidence` admission. Until then this ADR stays Proposed and mutable-branch evidence must not be promoted to integrated product truth.
