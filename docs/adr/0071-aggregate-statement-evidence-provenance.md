# ADR 0071: Aggregate statement evidence provenance

- Status: Proposed
- Date: 2026-09-22

## Problem

`aggregate_allocations()` produces reviewable Bank Reconciliation allocation evidence. The journal side already requires one exact repository-owned `BookJournalEvidence`, but the statement side still accepts caller-assembled `(statement_entry_reference, Decimal amount)` pairs.

Those pairs can conserve money exactly while severing the allocated amount from the statement currency, CRDT/DBIT movement direction, source references, and source dates that were admitted with the bank statement evidence. A caller can therefore present a numerically consistent aggregate without proving that the statement money belongs to source evidence compatible with the journal being allocated.

This is inconsistent with the existing deterministic reconciliation boundary. `StatementEntryEvidence` and `BookJournalEvidence` both admit exact money, canonical currency, and CRDT/DBIT direction, and deterministic matching requires currency and direction to agree before a proposal can be returned. Aggregate allocation must not weaken that source-evidence boundary merely because exact totals happen to conserve.

## Constraints

- Bank Reconciliation remains proposal/evidence authority only. This decision grants no journal posting, reversal, reconciliation approval, Period Close, account-selection, Accounting Policy, Billing, or LLM authority.
- Aggregate output remains immutable `ReconciliationAllocation` evidence with exact built-in positive `Decimal` money and tenant/run/source identity.
- The statement population remains an exact built-in tuple so mutable or caller-behavior-bearing population containers cannot enter allocation planning.
- Statement source evidence remains owned by `StatementEntryEvidence`; journal source evidence remains owned by `BookJournalEvidence`.
- Fields used as executable allocation controls are revalidated at the point of use because a frozen Python value object is not a tamper-proof boundary against low-level mutation or deserialization.
- Exact monetary conservation and distinct source-identity invariants remain mandatory.
- This is a repository runtime-domain and provenance decision. ISO 20022 supplies statement vocabulary such as `CdtDbtInd`; it does not prescribe this Python object boundary or aggregate allocation algorithm.
- Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain the #37 single-writer surface and are not modified in this lane.

## Alternatives considered

### Keep `(statement_entry_reference, Decimal amount)` pairs

Rejected. Identity and amount alone cannot prove the currency or movement direction bound to the bank statement source. Exact conservation would continue to accept caller-assembled provenance that is weaker than the repository's normalized statement evidence.

### Add independent statement currency and direction scalars

Rejected. This merely reconstructs source evidence from more caller-provided fields. A mutually consistent set of scalars still does not prove that one admitted statement source owns those values.

### Accept a statement-shaped protocol or duck-typed object

Rejected. Allocation planning would execute caller-defined attribute behavior and bypass the exact identity, money, currency, direction, reference, and date admission performed by `StatementEntryEvidence`.

### Require exact `StatementEntryEvidence` members

Selected. Typed callers provide an exact built-in tuple whose members are exact repository-owned `StatementEntryEvidence`. The planner admits each member before reading attributes, revalidates the identity, amount, currency, and CRDT/DBIT direction used by allocation control, requires statement currency and direction to equal the admitted journal evidence, preserves distinct statement identities, and then proves exact monetary conservation.

## Decision

`aggregate_allocations()` must consume repository-owned statement evidence rather than scalar statement pairs.

- The public typed contract is `statement_items: tuple[StatementEntryEvidence, ...]`.
- Runtime admission requires `type(statement_items) is tuple` before population iteration and `type(statement) is StatementEntryEvidence` before member attribute reads.
- Caller-assembled `(statement_entry_reference, Decimal amount)` pairs, duck-typed statement objects, statement subclasses, mutable populations, and tuple subclasses fail closed.
- Each admitted statement identity is revalidated and may occur at most once in one aggregate proposal.
- Each statement amount is revalidated as an exact finite positive built-in `Decimal` immediately before arithmetic.
- Each statement currency is revalidated using the accounting core's canonical three-uppercase-letter syntax and must equal the admitted journal currency.
- Each statement `credit_debit_code` is revalidated as exact `CRDT` or `DBIT` and must equal the admitted journal direction.
- The journal identity, amount, currency, and direction are also revalidated at the allocation boundary before comparison.
- Statement-side total must equal the admitted journal amount exactly. No tolerance, coercion, or rounding is introduced.
- No database schema or migration changes are required; this decision narrows only the in-memory proposal contract.

## Evidence

RED commit `d38e13a9cac9683102a1d1bf70b66c6ad177e49f` was created from parent #161 exact `377f39cd798e3351993aee5b785d1f2a7f31e1fb`. It holds tenant/run scope, one `1000.00 KRW` debit journal, and exact conservation fixed while varying only statement provenance. The RED requires exact `StatementEntryEvidence`, rejects the historical scalar-pair shape, and fails closed for cross-currency and opposite-direction statement populations.

Causal production repair `b3cb6b842297786d295ca54570a96f37762afb3f` changes the aggregate typed/runtime statement boundary to exact `StatementEntryEvidence`, revalidates statement and journal allocation controls at use, and requires statement currency and direction to agree with the journal before exact conservation. It does not alter split allocation, persistence, approval, Posting, Period Close, Accounting Policy, or Billing truth.

Ordinary descendant commits adapt predecessor conservation and journal-side regression fixtures so those tests continue to exercise their original controls through the stronger statement-evidence boundary. They do not transfer predecessor review or hosted-runtime evidence to this decision.

## Compatibility, risks, and effects

This intentionally narrows a public Python call shape. Callers that previously supplied `(statement_entry_reference, Decimal amount)` pairs must obtain or construct admitted `StatementEntryEvidence` and pass those value objects. That compatibility cost is deliberate: exact totals alone are insufficient source provenance for reviewable reconciliation evidence.

This decision still does not prove that an in-memory `StatementEntryEvidence` or `BookJournalEvidence` came from the current authoritative PostgreSQL snapshot. Persistence, approval, lifecycle, and close-package owners remain responsible for database snapshot binding, lock ordering, tenant scope, recovery, and immutable approval evidence.

A mismatch now fails earlier and more specifically: foreign currency and opposite CRDT/DBIT direction cannot survive to conservation merely because the numeric total ties. The result is stricter proposal admission, not new accounting authority.

## Follow-up

ADR 0054's Allocation conservation section and ADR 0069's statement-population constraint predate this decision and must be interpreted as superseded by ADR 0071 for aggregate statement-side admission. The canonical Bank Reconciliation owner path should make those older descriptions code-current without weakening their split, conservation, or journal-provenance controls.

PR #37 remains the canonical single writer for shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md`. After protected integration, #37 must rebuild those records from the exact protected tree and trace the progression from scalar statement pairs to exact `StatementEntryEvidence` admission. This ADR remains Proposed until exact-head review, hosted test/security evidence, and the owner-path documentation gate are satisfied.
