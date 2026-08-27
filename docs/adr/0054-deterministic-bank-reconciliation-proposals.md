# ADR 0054: Deterministic bank-reconciliation proposals

- Status: Proposed
- Date: 2026-08-26

## Context

The immutable `camt.053.001.14` bank-statement registry is an integrated accounting-information-platform fact. The next bounded reconciliation capability must compare that evidence with immutable posted-book evidence without allowing a statement line, a heuristic, or an LLM to write accounting facts.

ISO 20022 defines the interoperable message model and the Registration Authority currently publishes `camt.053.001.14` as `BankToCustomerStatementV14`. Those authorities define the source evidence vocabulary and schema; they do **not** prescribe this platform's journal-matching algorithm. The matching precedence below is therefore an AIS control decision, not a claim of ISO conformance for reconciliation behavior. The Registration Authority catalogue was rechecked on 2026-08-26 and still lists `camt.053.001.14` in the Bank-to-Customer Cash Management message set last updated 19 March 2026.

## Decision

Add a pure proposal-only deterministic reconciliation boundary. It accepts normalized statement evidence, read-only posted-journal evidence, and a bounded date policy and returns either one reviewable proposal or an explicit abstention.

The first bounded precedence is:

1. a present provider reference;
2. otherwise a present end-to-end reference;
3. otherwise a present account-servicer reference;
4. only when no strong identity is present, exact amount, currency, CRDT/DBIT economic direction, and the configured booking/accounting-date window;
5. otherwise abstain.

A higher-confidence identity conflict never falls through to a weaker rule. A strong identity is not sufficient by itself: amount, currency, and credit/debit direction must also agree exactly. Duplicate candidates remain ambiguous even when money and direction agree. The weaker money/date rule is permitted only for one unique same-direction candidate. Reconciliation monetary evidence is canonical only when it is a finite, strictly positive `Decimal`; binary floating-point values, zero, negative values, `NaN`, and infinities fail before candidate comparison. CRDT/DBIT carries economic direction separately, so the engine never infers direction from a signed amount or coerces/rounds monetary evidence.

Every abstention carries an exception code and an operator next action. The current bounded codes are `ambiguous_reference`, `amount_mismatch`, `currency_mismatch`, `direction_mismatch`, `date_window_mismatch`, and `no_candidate`.

Every returned decision, including an abstention, retains the immutable `statement_entry_reference` that produced it. This keeps a proposal attributable when it is logged, exported, or later persisted; caller context alone is not treated as durable audit provenance.

The decision object is a proposal only. It does not persist a reconciliation approval, mutate statement evidence, post or reverse a journal, select a chart account, or alter accounting policy. Any future adjustment must enter the existing accounting command boundary with its own idempotency identity, immutable source evidence, period/policy/authorization checks, and authoritative posting receipt.

## Consequences and limits

The statement-side direction is the normalized ISO 20022 `CdtDbtInd` evidence already retained by the integrated statement registry. Both statement-side and book-side reconciliation evidence reject any direction code other than `CRDT` or `DBIT` before matching begins, so two equally invalid arbitrary strings can never become a match. They also reject non-`Decimal`, non-finite, zero, or negative monetary evidence before matching; direction stays a separate CRDT/DBIT fact rather than being encoded as a sign. Book candidates must expose the corresponding economic cash-movement direction explicitly; amount/reference equality alone cannot reconcile an incoming bank credit to an outgoing book movement or vice versa. A direction conflict fails closed before a proposal is emitted.

This slice intentionally does not claim the complete issue #8 reconciliation vertical. Persistence of immutable reconciliation runs/candidates, many-to-many allocation conservation, explicit approval/exception records, concurrency protection, exact book-to-bank bridge equations, temporal knowledge cutoffs, close evidence, and exports remain later bounded work and must be test-first before they can be treated as integrated capability.

LLM or probabilistic output may later summarize or prioritize an exception, but it cannot invoke this proposal as an approval, consume monetary evidence, or post an adjustment.

## Evidence

The initial RED contract was executed on exact PR head `80ce0eb1cffb4b60199d22ff20830abc985bc7d3`: PostgreSQL 18.4 foundation behavior ran, then all six initial reconciliation tests failed at the same first causal boundary because `accounting_information_platform.reconciliation` did not exist. The production implementation was added only after that observed RED boundary. Later exact head `d7a96af21698a15f8722f63bc51b76fdb12c56de` added the CRDT/DBIT regression contract after comparing this proposal model with the integrated normalized statement model.

Exact predecessor `73349aeb6973fa26fa97fe7c8f132aa79ced0aca` then failed Accounting Foundation CI `32973939075` at behavior/repository tests after exact-head SAST/security/dependency jobs succeeded; all later coverage/package evidence was skipped. The bounded defect was missing source-statement provenance on `ReconciliationDecision`. The narrow repair makes `statement_entry_reference` mandatory on both match and abstention results. Predecessor execution evidence does not transfer to later heads.

Current-head review also identified that arbitrary direction strings could be compared as if they were normalized evidence. The regression contract now requires invalid statement and book direction codes to fail before matching; only current-head execution may establish that repair as passing evidence.

Exact RED head `b1dc468dc42591a4d92dc96957dc905e036d12c1` then ran 404 PostgreSQL-backed behavior/repository tests and failed six new monetary-domain cases before coverage or package evidence could run: binary float, zero, negative, `NaN`, positive infinity, and negative infinity were all accepted by the typed evidence constructors because type annotations were not runtime authority. The narrow repair at `b2fdb5cdaa24e4735e5961e29f0b310bb8560349` rejects every value except a finite, strictly positive `Decimal` at the evidence boundary before matching. Execution evidence belongs only to the exact head that produced it and is not transferred to later documentation heads.

## References

See `docs/doctoring/REFERENCES.md` for APA 7 entries covering ISO 20022-1:2026, ISO 20022-4:2026, ISO 20022-9:2026, and the ISO 20022 Registration Authority `camt.053.001.14` catalogue.
