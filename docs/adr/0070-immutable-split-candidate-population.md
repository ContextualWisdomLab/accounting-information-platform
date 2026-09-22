# ADR 0070: Immutable split candidate population admission

- Status: Proposed
- Date: 2026-09-22

## Problem

ADR 0054 requires every split candidate to be exact repository-owned `BookJournalEvidence`, but `propose_split_allocations()` still accepted `Iterable[BookJournalEvidence]` and executed `tuple(candidate_journals)` before the first population-domain check.

That left the population container outside the reconciliation evidence boundary. A mutable list was silently snapshotted and accepted. A caller-defined iterable could execute arbitrary iteration behavior before the planner had established that it was consuming immutable repository-shaped evidence. Exact member admission therefore did not prove that the source population itself was immutable or free of caller-defined iteration semantics.

The aggregate side already rejects non-built-in tuple populations before iteration. Keeping split planning weaker creates an avoidable source-provenance asymmetry inside the same Bank Reconciliation bounded context.

## Constraints

- Bank Reconciliation remains proposal/evidence authority only. This decision grants no reconciliation approval, journal posting/reversal, Period Close, Accounting Policy, account-selection, or Billing authority.
- Every split member must still be exact `BookJournalEvidence`; exact positive `Decimal`, distinct journal identity, same-currency, tenant/run/statement scope, and exact conservation invariants remain unchanged.
- No schema, migration, persistence, release, or LLM authority changes are introduced.
- A compatibility narrowing must fail through a repository-owned `ValueError`, not by executing caller-defined iteration and leaking its exception.
- Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain the #37 single-writer surfaces and are updated only from protected integrated truth.

## Alternatives considered

### Keep accepting arbitrary `Iterable[BookJournalEvidence]`

Rejected. Converting an arbitrary iterable to a tuple is already execution of caller-owned container behavior. The resulting tuple may be immutable, but that does not make the admission path itself immutable or provenance-safe.

### Accept lists and other iterables, then copy them before validation

Rejected. A defensive copy prevents later mutation of the copied container but still executes caller-defined iteration before domain admission and silently broadens the accepted API to mutable source populations.

### Require an exact built-in tuple before iteration

Selected. `type(candidate_journals) is tuple` is checked before emptiness, member iteration, attribute access, or arithmetic. Typed callers publish `tuple[BookJournalEvidence, ...]`. Exact member-type admission then runs as before.

## Decision

`propose_split_allocations()` accepts the split candidate population only as an exact built-in tuple.

- Mutable lists, tuple subclasses, generators, and caller-defined iterables are not valid split population evidence.
- Rejection occurs before population iteration, so custom `__iter__` behavior cannot execute inside allocation planning.
- Exact built-in tuples continue through the existing exact-`BookJournalEvidence` member guard, distinct-journal check, exact-money validation, one-currency check, and conservation equation.
- The returned `ReconciliationAllocation` shape and all accounting authority boundaries are unchanged.

This is a repository runtime-domain invariant. It is not an ISO 20022 or IFRS conformance claim.

## Evidence

RED `ee3fcf7c281220d6d0040aa27603da91d7518a22` is based on #160 exact `5750f1c1b41b673ea0ca978bd30205872ac83c7f`. It holds one canonical `1000.00 KRW` `BookJournalEvidence`, statement identity, tenant/run scope, and exact conservation constant while varying only the population container. The exact built-in tuple is the positive control. A mutable list must fail rather than be silently snapshotted, and a caller-defined iterable whose `__iter__` raises must fail with the repository-owned population-domain `ValueError` before that iterator executes.

Production repair `27abee38748d8b1afbe9257eac72241c1e54d5e1` narrows the typed parameter to `tuple[BookJournalEvidence, ...]`, checks exact tuple type before iteration, removes the arbitrary-`Iterable` import, and otherwise preserves the split algorithm.

Hosted execution, coverage, security, and review evidence are exact-head-specific and are not inferred from source inspection or predecessor PRs.

## Risks and compatibility effect

Callers that intentionally pass lists, generators, tuple subclasses, or other iterable wrappers must construct an exact built-in tuple before entering the reconciliation planner. That is an intentional compatibility narrowing: the caller chooses the snapshot boundary explicitly instead of allowing the accounting domain to execute an unadmitted source container while manufacturing one.

The remaining aggregate statement-side scalar-pair contract is a separate ADR 0054 decision and is not changed here.

## Follow-up

Keep this decision `Proposed` until the exact descendant has current behavior/security evidence and qualifying review, then let the canonical documentation owner rebuild shared changelog/traceability/product-gap records from the protected integrated tree. Any later redesign that binds aggregate statement items to repository-owned `StatementEntryEvidence` requires its own compatibility decision and RED; it must not be smuggled into this container-domain repair.
