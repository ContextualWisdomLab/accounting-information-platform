# ADR 0073: Context-independent allocation conservation

- Status: Proposed
- Date: 2026-09-22

## Problem

Bank Reconciliation split and aggregate planning claim exact `Decimal` conservation, but the implementation accumulated source amounts with ordinary `Decimal` addition. Python `Decimal` arithmetic inherits the active thread-local decimal context, including precision. A caller or dependency can therefore change the result of the conservation calculation without changing any admitted accounting evidence.

This is observable inside the platform's durable `numeric(38, 6)` envelope. With a 28-digit context, `123456789012345678901234567890.123456 + 0.000001` rounds to `123456789012345678901234567900`, so a mathematically non-conserving source population can compare equal to a statement or journal target of `123456789012345678901234567900.000000`. A sufficiently low precision can also reject a mathematically conserving population. Both outcomes violate the deterministic exact-value contract.

## Constraints

- Split and aggregate allocation remain proposal/evidence functions. This decision grants no reconciliation approval, journal posting/reversal, Period Close, Accounting Policy, Billing, or LLM authority.
- Source amounts remain exact built-in finite positive `Decimal` values and are revalidated before arithmetic.
- Currency, CRDT/DBIT direction, source identity, immutable population shape, and distinct-source controls remain unchanged.
- Conservation must be independent of caller-owned decimal precision, rounding mode, flags, and traps.
- The fix must not coerce, quantize, tolerance-match, or silently round financial values.
- PostgreSQL remains authoritative durable storage with the platform's `numeric(38, 6)` milestone contract. This repair addresses the Python proposal boundary and does not change schema precision or scale.

## Alternatives considered

### Continue using the ambient decimal context

Rejected. Exact conservation would remain process-state dependent and could manufacture a match or reject a valid one solely because unrelated code changed decimal precision.

### Set a fixed low or default precision around allocation arithmetic

Rejected. A hard-coded precision such as Python's common 28 digits is below the durable accounting domain and would still round valid `numeric(38, 6)` values. A fixed precision larger than the current milestone would also make the exactness argument depend on an undocumented upper bound.

### Quantize amounts before summation

Rejected. Reconciliation is not a rounding-policy boundary. Quantization would change admitted source values and could hide source-evidence differences.

### Derive a fresh exact Decimal context from the admitted operands

Selected. After every amount has passed exact built-in finite-positive admission, derive the precision needed to retain every decimal position represented by the population plus conservative carry growth. Sum inside a fresh `decimal.Context`, not a copy of the caller context, and trap `Inexact`. If the Decimal runtime cannot evaluate the population exactly within its arithmetic domain, fail with a repository-owned `ValueError` rather than returning reviewable allocation evidence.

## Decision

`allocation._sum_exact_positive()` owns proposal-side conservation accumulation.

- It receives only amounts that have already passed `_require_exact_positive()`.
- It computes the minimum represented exponent and maximum adjusted decimal position from immutable Decimal tuples without arithmetic.
- It adds conservative carry precision based on population cardinality.
- It creates a fresh `Context(prec=required_precision)` so caller precision, rounding settings, flags, and traps are not inherited.
- It traps `Inexact`; rounding that would alter the numerical value cannot become reconciliation evidence.
- Decimal-domain failures are translated to the allocation API's `ValueError` boundary.
- Split planning collects validated journal amounts and compares their context-independent exact sum with the admitted statement amount.
- Aggregate planning collects validated statement amounts and compares their context-independent exact sum with the admitted journal amount.

No database migration is required. No source amount is normalized or rewritten.

## Evidence

RED `210f957c42341707c8e974e051c75d84bcf6a11d` is an ordinary child of #163 exact `1ba0ef041eba0827c15ac9a1d2bd5a54208fce3f`. It uses values inside `numeric(38, 6)` and proves three cases: a 28-digit ambient context must not round a non-conserving split into a match; the same must hold for aggregate planning; and a six-digit ambient context must not turn a mathematically exact split into a false mismatch. A separate 50-digit proof context establishes the mathematical control values in the test itself.

Production descendant `8d8b4c746f69c929c31b1c35c13277dde7b8165d` adds the context-independent summation boundary and replaces ambient-context accumulation on both split and aggregate paths. No source-provenance, currency, direction, approval, persistence, posting, close, or Billing authority changes are included.

No hosted RED or GREEN evidence is inferred from these source commits. Exact-head workflow evidence remains separate and must be attached only after a workflow executes on the unchanged final head.

## Compatibility, risks, and effects

For ordinary accounting amounts, the public API and returned allocation shape do not change. Behavior changes only where ambient Decimal context previously affected conservation or where the Decimal runtime cannot complete an exact sum without an arithmetic-domain failure.

The repair may allocate more Decimal precision than the caller selected because that caller setting is no longer allowed to weaken accounting correctness. Precision is derived from admitted operand representation and population cardinality, not from a user-controlled rounding policy.

## Follow-up

ADR 0054's Allocation conservation section must state that split and aggregate totals are accumulated in a fresh context-independent exact arithmetic boundary before equality is evaluated. Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain #37 single-writer surfaces and must be rebuilt only from protected integrated truth.

Keep this ADR Proposed until current-exact review, hosted foundation/security evidence, qualifying independent review, and owner-path documentation gates are satisfied.
