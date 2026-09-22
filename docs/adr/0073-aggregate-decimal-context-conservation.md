# ADR 0073: Context-independent aggregate allocation conservation

- Status: Proposed
- Date: 2026-09-22

## Problem

`aggregate_allocations()` validates exact repository-owned `StatementEntryEvidence` and `BookJournalEvidence`, then proves many-statement-to-one-journal conservation. The historical proof accumulated statement amounts with ordinary `Decimal` addition before comparing the result to the journal amount.

Python `Decimal` addition obeys the active decimal context. With precision 28, `10000000000000000000000000000 + 1` rounds back to `10000000000000000000000000000`. The aggregate planner could therefore accept an overallocated statement population whose exact mathematical total exceeded the journal evidence by one unit. The inverse failure is also possible: a truly conserved `10000000000000000000000000001` journal can be rejected under a small ambient precision because the statement-side accumulator rounds before equality.

This contradicts ADR 0004's requirement that rounding be explicit policy rather than an implicit language/runtime default and ADR 0054's exact allocation-conservation contract.

## Constraints

- Aggregate allocation remains proposal/evidence logic only. It gains no posting, reversal, reconciliation approval, Period Close, chart-account selection, Accounting Policy, Billing, or LLM authority.
- The exact `StatementEntryEvidence` population, exact `BookJournalEvidence`, identity, currency, CRDT/DBIT direction, positive finite built-in `Decimal`, and distinct-source admission rules remain unchanged.
- Conservation is mathematical equality. No tolerance, quantization, normalization, or implicit rounding is permitted.
- Caller or process `decimal` precision must not change whether the same admitted evidence conserves.
- The repair must reuse the exact-sum primitive introduced by the immediately preceding split-conservation repair rather than fork a second arithmetic implementation.
- Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain #37 single-writer surfaces.

## Alternatives considered

### Raise the Decimal precision locally

Rejected. Choosing a large fixed precision only moves the failure threshold. Computing a dynamic precision from magnitude and scale still makes the proof depend on decimal-context arithmetic and duplicates logic for a property that can be checked exactly with integers.

### Quantize to storage scale before comparison

Rejected. Reconciliation source evidence is already admitted as exact `Decimal`. Quantization would introduce a rounding policy into proposal logic and could hide a source mismatch. ADR 0004 requires rounding and scale treatment to be explicit versioned policy.

### Compare exact scaled integer coefficients

Selected. After every monetary input has passed exact finite positive built-in `Decimal` admission, reuse `_exact_decimal_sum_matches()`. It chooses the minimum base-10 exponent across the admitted values and expected amount, converts each Decimal coefficient to an exact Python integer at that exponent, sums Python integers, and compares the resulting integer with the expected scaled coefficient. No `Decimal` addition or ambient precision participates in the conservation decision.

## Decision

`aggregate_allocations()` must collect the already validated statement amounts and evaluate conservation with `_exact_decimal_sum_matches(tuple(statement_amounts), book_total)`.

The helper remains private to the allocation module. It does not widen the input domain: aggregate money must still be exact built-in, finite, positive `Decimal` evidence before the helper is reached. The existing error remains a repository-owned `ValueError` when exact statement-side and journal-side totals differ.

## Evidence

RED `729f338002a9d1cd8f5e066dc92e24f9b2bc86fa` fixes repository-owned statement/journal evidence, tenant/run scope, KRW currency, and DBIT direction while varying only the arithmetic magnitude and ambient context. It requires:

- journal `10000000000000000000000000000` versus statements `10000000000000000000000000000` and `1` to fail at precision 28 rather than round into false equality; and
- journal `10000000000000000000000000001` versus those same statement amounts to remain valid at precision 2.

Causal repair `dc78fc7af2cdf073b1aa380795c2b18492dc5c79` replaces the aggregate `Decimal` accumulator with the same exact scaled-integer comparison already used by split conservation. Split behavior, evidence admission, persistence, approval, Posting, Period Close, Accounting Policy, and Billing authority are unchanged.

Hosted test/security evidence is exact-head-specific and must not be inferred from source inspection or the parent PR.

## Compatibility, risks, and effects

The public call shape is unchanged. Calls whose exact mathematical totals already agree continue to agree regardless of ambient Decimal precision. Calls that were accepted only because Decimal context rounded an overallocated population now fail closed; calls that were rejected only because context rounding destroyed an exact equality now succeed.

The integer scaling step can materialize a coefficient at the smallest admitted exponent. This is consistent with exact arithmetic over the already admitted financial values and avoids hidden rounding. Any future tightening of reconciliation amount magnitude/scale must be decided at the source-evidence admission boundary, aligned with ADR 0004 and the PostgreSQL `numeric(38, 6)` storage contract, rather than by reintroducing rounded conservation.

## Follow-up

ADR 0054's shared Allocation conservation statement is now executable for both split and aggregate proposal paths once this PR is integrated. PR #37 must rebuild the shared changelog, traceability, and product-technical baseline from the exact protected tree after integration and trace the split RED `d82592b...`, split repair `019fed6...`, aggregate RED `729f338...`, and aggregate repair `dc78fc7...`.

This ADR remains Proposed until current-head review, hosted test/security evidence, and the owner-path documentation gate are satisfied.
