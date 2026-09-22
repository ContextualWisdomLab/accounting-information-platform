# ADR 0069: Explicit aggregate allocation source provenance

- Status: Proposed
- Date: 2026-09-22

## Problem

`aggregate_allocations()` returns reviewable Bank Reconciliation allocation evidence. Before this decision, callers could omit both `journal_reference` and `currency_code`; the planner then manufactured `journal-aggregate` and `KRW`. Exact monetary conservation did not prove either value came from the journal evidence being reconciled.

A synthetic journal identity can make unrelated aggregate proposals appear to share one source, while an assumed currency can label exact money with a value that was never supplied by source evidence. Neither behavior is acceptable provenance for a control object that may later be persisted or reviewed.

## Constraints

- Bank Reconciliation remains proposal/evidence authority only. This decision grants no journal posting, reversal, approval, period-close, account-selection, or accounting-policy authority.
- `ReconciliationAllocation` remains exact-`Decimal`, tenant/run/source scoped and immutable.
- The aggregate statement population remains ADR 0054's exact built-in tuple of exact built-in `(statement_entry_reference, Decimal amount)` pairs; this decision does not redesign that contract.
- Currency syntax remains the accounting core's exact built-in three-uppercase-letter repository rule. This does not claim a mutable ISO 4217 catalogue.
- The package is typed (`py.typed`); omission sentinels needed only for repository-owned runtime errors must not be advertised as accepted public provenance types.
- Existing callers that intentionally aggregate evidence must bind the journal identity and currency explicitly rather than inherit a convenience default.

## Alternatives considered

### Keep `journal-aggregate` and `KRW` defaults

Rejected. These values are convenient placeholders, not source evidence. Their presence makes a successfully conserved allocation look more authoritative than the caller actually proved.

### Require Python keyword arguments with no runtime defaults

Rejected as the runtime domain boundary. Python would raise `TypeError` before the reconciliation domain can return the same fail-closed `ValueError` family used for malformed journal identity and currency.

### Advertise `str | None` as the public typed contract

Rejected. `None` is an implementation sentinel for domain-owned omission errors, not valid reconciliation provenance. Advertising it would cause typed callers to lose a useful diagnostic and defer a malformed binding to runtime.

### Require `str` in typed overloads while retaining `None` only in the runtime implementation

Selected. Typed callers must supply string journal identity and currency. At runtime, omission can still reach repository-owned validation: missing `journal_reference` fails `_require_identity()` and missing `currency_code` fails `_require_allocation_currency()`. No synthetic financial provenance is created, and the sentinel is not part of the accepted type contract.

## Decision

`aggregate_allocations()` must not synthesize journal source provenance.

- Public overloads require `journal_reference: str` and `currency_code: str`; omission and explicit `None` are not accepted typed calls.
- The runtime implementation defaults these two arguments only to the absence sentinel `None`, solely so omitted bindings fail through repository-owned domain validation rather than Python argument binding.
- Validation occurs before statement-population iteration and before any `ReconciliationAllocation` is returned.
- Explicit valid values preserve existing exact conservation, distinct-statement identity and immutable result semantics.

## Evidence

The realistic RED is `dea57479af8a9fa5f17d9adaa4c1aec095711433` on parent #158 exact `530f179b6e3493916bfe920285caf3d040b7f3f8`. It keeps one `1000.00` exact-Decimal statement item, one `1000.00` journal total, tenant/run scope and all population shape invariants constant. Explicit `journal-001` / `USD` remains the positive control; omission of only the journal identity or only the currency must fail closed. Parent behavior returns allocations with the synthetic defaults instead.

The causal runtime repair is `b49dc34c81c4e9f4c7232bd5e3ea480980fa6861`. It replaces the two synthetic defaults with `None`, routes omission through the existing journal-identity and allocation-currency validators, and documents the explicit-binding contract. No schema, migration, persistence approval, Posting, Period Close, Accounting Policy, Billing truth, or LLM authority changes.

Consumer fixtures that exercised aggregate conservation or numeric-domain behavior without source bindings are adapted by ordinary descendants `167167485720429c04b48f73007f9144ecacc53b` and `20817992b324bf96631be32df52af4ff0bafe33c` to supply explicit `journal-001`/`journal-a` and `KRW`. Their accounting assertions are otherwise unchanged.

Current-exact review then identified that the first runtime repair exposed `str | None` in the typed signature even though `None` always fails. RED `723a05e729224b58add1e471d67b55f87e9bdcbe` inspects the published overload registry and requires every accepted overload to expose required `str` journal/currency bindings. Repair `feeffa7b2e0ef7131071d92cca49b9cfe51ee7ac` adds required-string overloads while preserving the runtime sentinel and repository-owned `ValueError` path.

## Risks and effects

This intentionally narrows a public Python call shape: callers relying on implicit `journal-aggregate` or `KRW` now fail closed. Typed callers forwarding optional configuration also receive a type-checking diagnostic instead of seeing `None` presented as supported provenance. That is a compatibility cost, but it exposes missing provenance instead of silently manufacturing it.

The change does not prove that the supplied journal reference or currency was loaded from PostgreSQL-owned authority; it only removes the planner's ability to invent them. Persistence and close-package owners remain responsible for binding reviewed allocation evidence to their authoritative database snapshots and controls.

## Follow-up

PR #37 remains the canonical single writer for shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md`. After this decision reaches protected integration through the Bank Reconciliation owner path, #37 must rebuild those records from the exact protected tree and record the removal of synthetic aggregate journal provenance plus the required-string typed compatibility contract. Until then this ADR stays Proposed and mutable-branch evidence must not be promoted to integrated product truth.
