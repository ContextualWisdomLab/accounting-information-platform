# ADR 0054: Deterministic bank-reconciliation proposals

- Status: Proposed
- Date: 2026-08-26

## Context

The immutable `camt.053.001.14` bank-statement registry is an integrated accounting-information-platform fact. The next bounded reconciliation capability must compare that evidence with immutable posted-book evidence without allowing a statement line, a heuristic, or an LLM to write accounting facts.

ISO 20022 defines the interoperable message model and the Registration Authority currently publishes `camt.053.001.14` as `BankToCustomerStatementV14`. Those authorities define the source evidence vocabulary and schema; they do **not** prescribe this platform's journal-matching algorithm. The matching precedence below is therefore an AIS control decision, not a claim of ISO conformance for reconciliation behavior.

## Decision

Add a pure proposal-only deterministic reconciliation boundary. It accepts normalized statement evidence, read-only posted-journal evidence, and a bounded date policy and returns either one reviewable proposal or an explicit abstention.

The first bounded precedence is:

1. a present provider reference;
2. otherwise a present end-to-end reference;
3. otherwise a present account-servicer reference;
4. only when no strong identity is present, exact amount and currency plus the configured booking/accounting-date window;
5. otherwise abstain.

A higher-confidence identity conflict never falls through to a weaker rule. A strong identity is not sufficient by itself: amount and currency must also agree exactly. Duplicate candidates remain ambiguous even when money agrees. The weaker money/date rule is permitted only for one unique candidate. All monetary values remain `Decimal`; the engine does not round, coerce, or consume evidence on abstention.

Every abstention carries an exception code and an operator next action. The current bounded codes are `ambiguous_reference`, `amount_mismatch`, `currency_mismatch`, `date_window_mismatch`, and `no_candidate`.

The decision object is a proposal only. It does not persist a reconciliation approval, mutate statement evidence, post or reverse a journal, select a chart account, or alter accounting policy. Any future adjustment must enter the existing accounting command boundary with its own idempotency identity, immutable source evidence, period/policy/authorization checks, and authoritative posting receipt.

## Consequences and limits

This slice intentionally does not claim the complete issue #8 reconciliation vertical. Persistence of immutable reconciliation runs/candidates, many-to-many allocation conservation, explicit approval/exception records, concurrency protection, sign/direction fixtures, exact book-to-bank bridge equations, temporal knowledge cutoffs, close evidence, and exports remain later bounded work and must be test-first before they can be treated as integrated capability.

LLM or probabilistic output may later summarize or prioritize an exception, but it cannot invoke this proposal as an approval, consume monetary evidence, or post an adjustment.

## Evidence

The RED contract was executed on exact PR head `80ce0eb1cffb4b60199d22ff20830abc985bc7d3`: PostgreSQL 18.4 foundation behavior ran, then all six initial reconciliation tests failed at the same first causal boundary because `accounting_information_platform.reconciliation` did not exist. The production implementation was added only after that observed RED boundary. Predecessor execution evidence does not transfer to later heads.

## References

See `docs/doctoring/REFERENCES.md` for APA 7 entries covering ISO 20022-1:2026, ISO 20022-4:2026, ISO 20022-9:2026, and the ISO 20022 Registration Authority `camt.053.001.14` catalogue. The Registration Authority catalogue was last updated 19 March 2026 when rechecked for this decision.
