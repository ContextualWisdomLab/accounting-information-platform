# ADR 0072: Split statement evidence provenance

- Status: Proposed
- Date: 2026-09-22

## Problem

`propose_split_allocations()` produces reviewable Bank Reconciliation allocation evidence. Its journal side already requires an exact built-in tuple of exact repository-owned `BookJournalEvidence`, but its statement side still accepts caller-assembled `statement_entry_reference` and `statement_amount` scalars.

Those two scalars can conserve exactly while severing the allocated amount from the statement currency, CRDT/DBIT movement direction, source references, booking date, and value date admitted by `StatementEntryEvidence`. A caller can therefore pair `1000.00` of one statement identity with journals in a different currency or movement direction and still receive numerically conserved allocation evidence because the split API has no authoritative statement currency or direction to compare.

This is the split-side analogue of ADR 0071. Exact monetary conservation is necessary but not sufficient provenance for reconciliation evidence.

## Constraints

- Bank Reconciliation remains proposal/evidence authority only. This decision grants no journal posting, reversal, reconciliation approval, Period Close, chart-account selection, Accounting Policy, Billing, or LLM authority.
- Split output remains immutable `ReconciliationAllocation` evidence with exact built-in positive `Decimal` money and tenant/run/source identity.
- Candidate journal population remains an exact built-in tuple and each candidate remains exact repository-owned `BookJournalEvidence` under ADR 0070 and the earlier candidate-evidence decision.
- Statement source evidence remains owned by `StatementEntryEvidence`; journal source evidence remains owned by `BookJournalEvidence`.
- Fields used as executable allocation controls are revalidated at the point of use because a frozen Python value object is not a tamper-proof boundary against low-level mutation or deserialization.
- Exact monetary conservation and distinct journal identity remain mandatory.
- This is a repository runtime-domain and provenance decision. ISO 20022 supplies statement vocabulary such as `CdtDbtInd`; it does not prescribe this Python API or split allocation algorithm.
- Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain the #37 single-writer surface and are not modified in this lane.

## Alternatives considered

### Keep statement identity and amount scalars

Rejected. Identity and amount alone cannot prove the currency or movement direction bound to the bank statement source. Exact conservation can therefore produce reviewable evidence from a caller-assembled statement shape that is weaker than normalized source evidence.

### Add independent statement currency and direction scalars

Rejected. This reconstructs more source fields at the call site but still allows a mutually consistent scalar bundle that is not bound to one admitted `StatementEntryEvidence`.

### Accept a statement-shaped protocol or duck-typed object

Rejected. Split planning would execute caller-defined attribute behavior and bypass the repository admission already performed by `StatementEntryEvidence`.

### Require exact `StatementEntryEvidence`

Selected. Typed callers provide one exact repository-owned `StatementEntryEvidence`. The planner admits its exact type before attribute reads, revalidates statement identity, exact money, canonical currency, and CRDT/DBIT direction, requires every admitted journal candidate to match statement currency and direction, and then proves exact monetary conservation.

## Decision

`propose_split_allocations()` must consume repository-owned statement evidence rather than independent statement identity and amount scalars.

- The published overload contract requires `statement_evidence: StatementEntryEvidence` and an exact built-in tuple of `BookJournalEvidence` candidates. The overload surface omits the historical scalar keyword names; the implementation keeps them only as runtime rejection sentinels for stale callers.
- Runtime admission requires `type(statement_evidence) is StatementEntryEvidence` before statement attribute reads.
- Historical `statement_entry_reference` and `statement_amount` keyword names remain runtime-only sentinels so stale callers receive a repository-owned domain error. Supplying either keyword, including explicit `None`, fails closed even when canonical statement evidence is also present.
- Statement identity is revalidated immediately before allocation construction.
- Statement amount is revalidated as an exact finite positive built-in `Decimal` immediately before arithmetic.
- Statement currency is revalidated with accounting-core canonical three-uppercase-letter syntax.
- Statement `credit_debit_code` is revalidated as exact built-in `CRDT` or `DBIT`.
- Every journal candidate identity, amount, currency, and direction is revalidated at use.
- Every candidate journal currency and direction must equal the admitted statement evidence before conservation is evaluated.
- Journal identities must remain distinct and candidate amounts must sum to the statement amount exactly. No tolerance, coercion, or rounding is introduced.
- No database schema or migration changes are required; this decision narrows only the in-memory split proposal contract.

## Evidence

RED commit `942952d6362fa714a81dac6b1cb502de866c042d` was created from #162 exact `e77c4771d176ab686c958e276646b871ff30416c`. It holds tenant/run scope and an exactly conserved `1000.00 KRW` debit journal population constant while varying only statement provenance. The RED requires exact `StatementEntryEvidence`, rejects the historical scalar call shape, rejects cross-currency and opposite-direction statement evidence, and rejects a `StatementEntryEvidence` subclass before caller-defined attribute behavior can execute.

Causal production repair `ab5cb7135e0e7addda546db5b6fa72b1080199de` changes split statement admission to exact `StatementEntryEvidence`, retains legacy keyword sentinels solely for fail-closed compatibility errors, revalidates statement and journal allocation controls at use, and requires statement/journal currency and direction agreement before exact conservation.

Ordinary descendants adapt predecessor split-population, candidate-evidence, Decimal-runtime, and conservation fixtures so each continues to exercise its original invariant through the stronger statement-evidence boundary. `d9bafdf57ebd8fda93af9a54ee1ea58a7c63b713` additionally proves that explicitly supplied legacy keywords, including `None`, cannot be combined with canonical statement evidence. These descendants do not transfer predecessor review or hosted-runtime evidence to this decision.

Current-exact review then found that the ADR's typed-contract claim was not actually published: the implementation signature still exposed optional `statement_evidence` plus legacy scalar names to type checkers. Review RED `2a363c1331fd15e3343756c2e65d7435067350e7` adds the same `typing.get_overloads()` contract used by the aggregate API and requires mandatory `StatementEntryEvidence` with no legacy names. Production descendant `9866d45f59cb9bb505d06222e861f7894dfa5982` adds canonical split overloads while preserving the implementation-only sentinels for runtime migration errors. Documentation descendant `fbde16625982ce57ae875a31f35b3085a39388a3` currentizes ADR 0054's Allocation conservation contract with exact split statement evidence, currency/direction agreement, at-use revalidation, legacy rejection semantics, and ADR 0070/0071/0072 ownership. Hosted execution evidence remains exact-head-specific and is not inferred from source inspection.

## Compatibility, risks, and effects

This intentionally narrows a public Python call shape. Callers that previously supplied `statement_entry_reference` and `statement_amount` must obtain or construct admitted `StatementEntryEvidence` and pass that value object. The compatibility cost is deliberate: two scalars do not prove the source currency or movement direction that makes a split accounting-consistent.

The planner still does not prove that the in-memory statement or journal evidence belongs to the current authoritative PostgreSQL reconciliation snapshot. Persistence, approval, lifecycle, and close-package owners retain database snapshot binding, tenant scope, lock ordering, recovery, and immutable approval authority.

Cross-currency and opposite-direction splits now fail before monetary conservation can make them look valid. The effect is stricter proposal admission, not new accounting authority.

## Follow-up

ADR 0054's Allocation conservation section is code-current with this decision at `fbde16625982ce57ae875a31f35b3085a39388a3`; ADR 0070 continues to own split candidate-population immutability and ADR 0071 owns aggregate statement provenance.

PR #37 remains the canonical single writer for shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md`. After protected integration, #37 must rebuild those records from the exact protected tree and trace the progression from scalar split statement fields to exact `StatementEntryEvidence`, including the published overload contract, currency/direction compatibility, and at-use revalidation. This ADR remains Proposed until exact-head review, hosted test/security evidence, and the owner-path documentation gate are satisfied.
