# ADR 0054: Deterministic bank-reconciliation proposals

- Status: Proposed
- Date: 2026-08-26

## Context

The immutable `camt.053.001.14` bank-statement registry is an integrated accounting-information-platform fact. Reconciliation compares that evidence with immutable posted-book evidence without allowing a statement line, a heuristic, or an LLM to write accounting facts.

ISO 20022 defines the interoperable message model and the Registration Authority publishes `camt.053.001.14` as `BankToCustomerStatementV14`. Those authorities define the source-evidence vocabulary and schema; they do **not** prescribe this platform's journal-matching, allocation, approval, or book-to-bank control logic. Those controls are AIS decisions and are not claims of ISO conformance for reconciliation behavior.

## Decision

Use a proposal-only deterministic matching boundary. It accepts normalized statement evidence, read-only posted-journal evidence, and a bounded date policy and returns either one reviewable proposal or an explicit abstention.

The bounded precedence is:

1. a present provider reference;
2. otherwise a present end-to-end reference;
3. otherwise a present account-servicer reference;
4. only when no strong identity is present, exact amount, currency, CRDT/DBIT economic direction, and the configured booking/accounting-date window;
5. otherwise abstain.

A higher-confidence identity conflict never falls through to a weaker rule. A strong identity is not sufficient by itself: amount, currency, and credit/debit direction must also agree exactly. Duplicate candidates remain ambiguous even when money and direction agree. The weaker money/date rule is permitted only for one unique same-direction candidate. Reconciliation monetary evidence is canonical only when it is a finite, strictly positive `Decimal`; binary floating-point values, zero, negative values, `NaN`, and infinities fail before candidate comparison. CRDT/DBIT carries economic direction separately, so the engine never infers direction from a signed amount or coerces or rounds monetary evidence.

`DeterministicMatchPolicy.date_window_days` is runtime policy evidence, not a trusted Python type annotation. It must be a real non-negative integer. Boolean values, fractional values, and negative windows fail at policy construction before candidate comparison; `0` is the valid same-day-only policy.

Every abstention carries an exception code and an operator next action. The bounded codes are `ambiguous_reference`, `amount_mismatch`, `currency_mismatch`, `direction_mismatch`, `date_window_mismatch`, and `no_candidate`.

Every returned decision, including an abstention, retains the immutable `statement_entry_reference` that produced it. The deterministic proposal engine emits one matched journal per `match`; the durable close-review boundary also accepts a decision carrying the complete journal identity set for a persisted split/aggregate match. Every match carries a finite strictly positive exact `Decimal` allocation and no exception code. An `abstain` carries no matched journal, an exact zero `Decimal` allocation, and a non-empty exception code. Direct construction therefore cannot forge success-shaped close-review evidence.

The decision object is a proposal only. It does not mutate statement evidence, post or reverse a journal, select a chart account, close a period, or alter accounting policy. Any accounting adjustment must enter the existing accounting command boundary with its own idempotency identity, immutable source evidence, period/policy/authorization checks, and authoritative posting receipt.

### Exact book-to-bank bridge

The read-only bridge proves three equations independently with exact `Decimal` arithmetic:

1. `statement_opening_balance + statement_period_movements = statement_closing_balance`;
2. `book_opening_balance + posted_cash_book_movements = book_closing_balance`;
3. `reconciled_book_balance + outstanding_book_items - outstanding_bank_items = statement_closing_balance`.

All bridge monetary inputs must be finite `Decimal` values before any equation is evaluated. Finite zero and negative balances or movements remain valid because bridge populations can be signed. There is no tolerance rounding: a one-minor-unit difference remains an explicit exception. Every result retains reconciliation-run, immutable statement-population, posted-book-population, tenant, legal-entity, accounting-book, bank-account-assignment, and currency scope evidence. A reconciled bridge is close evidence only; it is not a journal command or an approval.

### Buyer close-review projection

A read-only buyer projection presents deterministic decisions and the exact bridge to a controller without creating a new accounting authority. It exposes bank closing balance, posted-book cash balance, reconciled balance, outstanding bank and book items, unexplained difference, deterministic match count, unresolved exception count and statement-entry references, and exact changes from a comparable preceding bridge run.

`Suitable for period-close review` requires a tying bridge plus a proven complete immutable statement-entry population. The decision population must contain exactly one decision for every expected statement entry; missing, duplicate, or extraneous identities fail closed. Close-review scope is not a caller assertion: the bridge-bound tenant, legal entity, accounting book, bank-account assignment, and currency must match the requested projection scope. A preceding-run delta is permitted only when both bridges are bound to the same immutable scope.

`Suitable for period-close review` is evidence eligibility only and is **not a reconciliation approval**, period-close command, journal-posting permission, or accounting-policy decision. Any bridge difference or unresolved exception makes the projection fail closed and emits a customer-facing next action naming what must be resolved before close review is repeated.

JSON and CSV exports preserve monetary values as decimal strings and keep immutable scope and population references visible. They never convert exact accounting evidence to binary floating point.

### Allocation conservation

A statement entry may reconcile against several journal candidates and several statement entries may reconcile to one journal total. Allocation plans use exact `Decimal` values and must conserve exact totals on both source sides. `ReconciliationAllocation` is immutable, tenant- and run-scoped, carries statement and journal identity plus currency, and rejects non-exact or non-positive money. Allocation plans remain evidence and never post, reverse, approve, close, or adjust a journal.

### Allocation persistence

Migration 0014 introduced normalized `reconciliation_candidate`, `reconciliation_match`, `statement_match_allocation`, and `journal_match_allocation` rows with forced tenant row-level security. Migration 0015 replaces the temporary run-wide single-approved-match restriction with database-owned multi-match conservation.

Migration 0015 permits multiple independent, split, or aggregate matches to become `approved` when their immutable evidence remains conserved. On every transition to `approved`, the database requires a non-empty statement allocation population and a non-empty journal allocation population for that match, and the exact sum of statement `allocated_amount` must equal the exact sum of journal `allocated_amount`. An approval with a missing side or unequal totals fails closed with `reconciliation_match_unbalanced` before it can consume source capacity.

Source capacity is conserved across active reconciliation runs under immutable tenant/accounting/bank scope. Approval serializes the statement and journal source identities with advisory transaction locks and rejects consumption beyond the authoritative candidate source amount. Only `approved` matches consume active capacity. An explicit transition to `rejected` or `superseded` releases capacity while preserving the historical candidate and allocation evidence.

Recorded candidates and statement/journal allocations are append-only. Candidate identity/capacity cannot be updated or deleted; allocation rows cannot be updated or deleted after recording, including after a match is superseded. Allocation rows may be inserted only while their match remains `proposed`; each source identity must be represented by a candidate in the same tenant/run before it can be allocated; once a match enters `approved`, `rejected`, or `superseded`, its reviewed allocation population is frozen even when unused source capacity remains. Allocation admission locks the parent `reconciliation_match` row, so a concurrent allocation cannot cross an uncommitted `proposed → approved` snapshot boundary: whichever transaction acquires that row first completes before the other re-evaluates the current match state. Corrections therefore use new evidence plus an explicit match-state transition rather than extending or rewriting reconciliation history.

These persistence controls grant no journal-posting, reversal, period-close, or accounting-policy authority. Migration 0016 now adds the separate durable reconciliation approval evidence and terminal state-machine control: PostgreSQL binds each decision to an exact candidate/allocation snapshot, freezes reviewed identity and late allocations, and fails closed on unbound legacy reviewed rows. Approval evidence remains reconciliation-control evidence and must not be treated as journal-posting, reversal, period-close, or accounting-policy authority.

## Consequences and limits

The statement-side direction is normalized ISO 20022 `CdtDbtInd` evidence. Both statement-side and book-side evidence reject any direction other than `CRDT` or `DBIT` before matching. Amount/reference equality cannot reconcile an incoming bank credit to an outgoing book movement or vice versa.

The bounded date window fails closed when malformed. Operators must supply zero or a whole non-negative number of days before reconciliation begins.

The currently integrated reconciliation vertical includes immutable statement evidence, deterministic proposal/abstention behavior, exact allocation planning, exact book-to-bank bridge and close-review projection, normalized run/exception evidence, candidate/match/allocation persistence, stable-source cross-run conservation, append-only candidate/allocation history, migration 0016's database-owned approval snapshot and terminal-state evidence, and migration 0017's parent-row-first approval/allocation lock-order repair. Remaining bounded work is limited to later operational close-package integration that has not yet been integrated into the protected branch. Such later work must remain test-first and may not be treated as integrated capability from predecessor or stacked evidence alone.

LLM or probabilistic output may summarize or prioritize an exception, but it cannot approve reconciliation, consume monetary evidence, post or reverse a journal, close a period, or alter accounting policy.

## Evidence

The initial deterministic-reconciliation RED contract ran on exact head `80ce0eb1cffb4b60199d22ff20830abc985bc7d3`; the PostgreSQL foundation ran and the initial tests failed because the reconciliation module did not exist. Later RED heads separately established CRDT/DBIT direction, source-statement decision provenance, fail-closed decision construction, exact bridge arithmetic, close-review population/scope, runtime monetary-domain validation, date-window policy validation, and bridge-bound accounting scope before each narrow repair.

For migration 0015, exact RED head `ba3e429be18397b3309aff7d725ec0d60d25c81a` ran PostgreSQL 18.4 and 477 behavior/repository tests. Exactly the two intended approval-balance regressions failed: a match with a missing journal-allocation side and a match whose statement and journal allocation totals differed. The equal non-empty control passed. The database guard was implemented only after that observed RED boundary. Existing reconciliation fixtures were normalized to the real lifecycle `proposed → allocations → approved`, preserving cross-run conservation, concurrency serialization, supersession-based capacity release, and append-only history.

Exact documentation RED head `d7e17676a76222a2e730b739275fd0afc0958700` then ran PostgreSQL 18.4 and 479 tests. Exactly two code-current documentation regressions failed: ADR 0054 still described the removed run-wide approval restriction/future multi-match persistence, and the `[Unreleased]` migration 0015 entry omitted the non-empty/equal allocation-side approval invariant. Coverage and package evidence did not become passing evidence for that RED head.

Execution evidence belongs only to the exact head that produced it and is not transferred to later heads.

## References

See `docs/doctoring/REFERENCES.md` for APA 7 entries covering ISO 20022-1:2026, ISO 20022-4:2026, ISO 20022-9:2026, the ISO 20022 Registration Authority `camt.053.001.14` catalogue, and PostgreSQL 18 explicit locking used for the approval/allocation serialization boundary.
