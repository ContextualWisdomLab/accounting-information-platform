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

Source-reference precedence operates only on validated identities. `statement_entry_reference` and `journal_reference` are required non-empty strings. `provider_reference`, `end_to_end_reference`, and `account_servicer_reference` are either absent (`None`) or non-empty strings on both statement and journal evidence. Blank whitespace or non-string values fail at evidence construction rather than becoming a truthy high-confidence reference, creating a blank-to-blank strong match, or blocking the weaker exact-money/date fallback.

Currency is runtime reconciliation evidence, not a trusted Python type annotation. Statement and book evidence both require the accounting core's canonical three-uppercase-letter currency syntax before any strong-reference or exact-money/date comparison. Blank, whitespace-only, lowercase, wrong-length, and non-string values fail at evidence construction, so equality between two malformed currency values cannot become match evidence. This is a repository syntax invariant; it does not claim that AIS maintains or validates a mutable ISO 4217 membership catalogue.

Credit/debit direction is runtime reconciliation evidence, not a trusted Python type annotation. Statement and book evidence both require an exact string `CRDT` or `DBIT` before any matching rule executes. Lowercase, blank, `None`, numeric, and unhashable collection values fail with the reconciliation domain's `ValueError` at evidence construction, so malformed direction cannot ride through a strong-reference match or leak a raw hash/membership `TypeError`.

Source calendar dates are also runtime reconciliation evidence, not trusted annotations. `booking_date`, `value_date`, and `accounting_date` must be exact calendar `date` values before any matching rule executes. String, numeric, `None`, and `datetime` values fail at evidence construction. A strong reference therefore cannot carry malformed date evidence into a reviewable match merely because that rule does not compare dates, and the weaker fallback cannot leak a raw subtraction `TypeError` from incompatible date-like values.

`DeterministicMatchPolicy.date_window_days` is runtime policy evidence, not a trusted Python type annotation. It must be a real non-negative integer. Boolean values, fractional values, and negative windows fail at policy construction before candidate comparison; `0` is the valid same-day-only policy.

Every abstention carries an exception code and an operator next action. The bounded codes are `ambiguous_reference`, `amount_mismatch`, `currency_mismatch`, `direction_mismatch`, `date_window_mismatch`, and `no_candidate`.

Every returned decision, including an abstention, retains the immutable non-empty `statement_entry_reference` that produced it. The deterministic proposal engine emits one matched journal per `match`; the durable close-review boundary also accepts a decision carrying the complete journal identity set for a persisted split/aggregate match. Every match carries a finite strictly positive exact `Decimal` allocation and no exception code. An `abstain` carries no matched journal, an exact zero `Decimal` allocation, and a non-empty exception code. Direct construction therefore cannot forge success-shaped close-review evidence or detach it from source identity.

Review guidance is part of the decision evidence, not presentation-only text. Every decision carries a non-empty `next_action` so exported or persisted evidence still tells the operator what review action remains permitted. A `match` also carries a non-empty `rule_code` identifying the deterministic or explicitly reviewed rule that produced the proposal. An `abstain` carries `rule_code = null`; it records the unresolved condition through `exception_code` rather than claiming a matching rule succeeded. These fields remain review provenance only and grant no approval, posting, reversal, period-close, or accounting-policy authority.

The direct-module `ReconciliationDecision` contract is explicitly versioned so that adding reviewed split evidence does not silently broaden the historical deterministic API. `reconciliation-decision/v1` is the default and preserves the original exactly-one-journal match invariant; every deterministic proposal and abstention remains v1. A caller that needs one reviewed statement decision to bind more than one persisted journal identity must opt into `reconciliation-decision/v2` explicitly. The v2 journal population is a set of non-empty immutable source identities: each `journal_reference` must be a non-blank string and may occur at most once in a decision. Repeating, blanking, or substituting a non-string journal identity fails closed rather than representing missing or duplicated evidence as reviewed split members. Unknown versions fail closed, and omitting the version never upgrades a singleton consumer to the multi-journal shape. This version marker is compatibility evidence only; it does not grant approval, posting, close, or accounting-policy authority.

The `matched_journal_references` container is itself immutable decision evidence. A frozen dataclass does not make an injected mutable list immutable, so direct construction accepts only a tuple for this population. A singleton v1 match, a multi-journal v2 reviewed match, and an empty abstention all retain tuple shape; list, `None`, or other mutable/container substitutions fail before decision branching. This prevents a caller from mutating the matched source population after construction and turning already-reviewed evidence into a different match or an abstention into journal-bearing evidence.

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

A statement entry may reconcile against several journal candidates and several statement entries may reconcile to one journal total. Allocation plans use exact `Decimal` values and must conserve exact totals on both source sides. `ReconciliationAllocation` is immutable, tenant- and run-scoped, carries statement and journal identity plus currency, and rejects non-exact or non-positive money. Within the pure proposal API, source identity is also a set invariant: one split may contain each `journal_reference` at most once, and one aggregate may contain each `statement_entry_reference` at most once. Repeating the same immutable source identity cannot manufacture additional apparent capacity even when the duplicated rows still sum exactly to the requested total. Allocation plans remain evidence and never post, reverse, approve, close, or adjust a journal.

### Allocation persistence

Migration 0014 introduced normalized `reconciliation_candidate`, `reconciliation_match`, `statement_match_allocation`, and `journal_match_allocation` rows with forced tenant row-level security. Migration 0015 replaces the temporary run-wide single-approved-match restriction with database-owned multi-match conservation.

Migration 0015 permits multiple independent, split, or aggregate matches to become `approved` when their immutable evidence remains conserved. On every transition to `approved`, the database requires a non-empty statement allocation population and a non-empty journal allocation population for that match, the exact sum of statement `allocated_amount` must equal the exact sum of journal `allocated_amount`, and every allocated source must participate in a candidate-proposed source pairing within that allocation population. An approval with a missing side, unequal totals, or an unproposed source pairing fails closed before it can consume source capacity.

Source capacity is conserved across active reconciliation runs under immutable tenant/accounting/bank scope. Approval serializes the statement and journal source identities with advisory transaction locks and rejects consumption beyond the authoritative candidate source amount. Only `approved` matches consume active capacity. An explicit transition to `rejected` or `superseded` releases capacity while preserving the historical candidate and allocation evidence.

Recorded candidates and statement/journal allocations are append-only. Candidate identity/capacity cannot be updated or deleted; allocation rows cannot be updated or deleted after recording, including after a match is superseded. Allocation rows may be inserted only while their match remains `proposed`; each source identity must be represented by a candidate in the same tenant/run before it can be allocated; once a match enters `approved`, `rejected`, or `superseded`, its reviewed allocation population is frozen even when unused source capacity remains. Allocation admission locks the parent `reconciliation_match` row, so a concurrent allocation cannot cross an uncommitted `proposed → approved` snapshot boundary: whichever transaction acquires that row first completes before the other re-evaluates the current match state. Corrections therefore use new evidence plus an explicit match-state transition rather than extending or rewriting reconciliation history.

These persistence controls grant no journal-posting, reversal, period-close, or accounting-policy authority. Migration 0016 now adds the separate durable reconciliation approval evidence and terminal state-machine control: PostgreSQL binds each decision to an exact candidate/allocation snapshot, freezes reviewed identity and late allocations, and fails closed on unbound legacy reviewed rows. Approval evidence remains reconciliation-control evidence and must not be treated as journal-posting, reversal, period-close, or accounting-policy authority.

## Consequences and limits

The statement-side direction is normalized ISO 20022 `CdtDbtInd` evidence. Both statement-side and book-side evidence reject any direction other than `CRDT` or `DBIT` before matching. Amount/reference equality cannot reconcile an incoming bank credit to an outgoing book movement or vice versa.

The bounded date window fails closed when malformed. Operators must supply zero or a whole non-negative number of days before reconciliation begins. Source booking/value/accounting dates themselves also fail closed unless they are exact calendar dates.

The currently integrated reconciliation vertical includes immutable statement evidence, deterministic proposal/abstention behavior, exact allocation planning, exact book-to-bank bridge and close-review projection, normalized run/exception evidence, candidate/match/allocation persistence, stable-source cross-run conservation, append-only candidate/allocation history, migration 0016's database-owned approval snapshot and terminal-state evidence, and migration 0017's parent-row-first approval/allocation lock-order repair. Remaining bounded work is limited to later operational close-package integration that has not yet been integrated into the protected branch. Such later work must remain test-first and may not be treated as integrated capability from predecessor or stacked evidence alone.

LLM or probabilistic output may summarize or prioritize an exception, but it cannot approve reconciliation, consume monetary evidence, post or reverse a journal, close a period, or alter accounting policy.

## Evidence

The initial deterministic-reconciliation RED contract ran on exact head `80ce0eb1cffb4b60199d22ff20830abc985bc7d3`; the PostgreSQL foundation ran and the initial tests failed because the reconciliation module did not exist. Later RED heads separately established CRDT/DBIT direction, source-statement decision provenance, fail-closed decision construction, exact bridge arithmetic, close-review population/scope, runtime monetary-domain validation, date-window policy validation, and bridge-bound accounting scope before each narrow repair.

For migration 0015, exact RED head `ba3e429be18397b3309aff7d725ec0d60d25c81a` ran PostgreSQL 18.4 and 477 behavior/repository tests. Exactly the two intended approval-balance regressions failed: a match with a missing journal-allocation side and a match whose statement and journal allocation totals differed. The equal non-empty control passed. The database guard was implemented only after that observed RED boundary. Existing reconciliation fixtures were normalized to the real lifecycle `proposed → allocations → approved`, preserving cross-run conservation, concurrency serialization, supersession-based capacity release, and append-only history.

Exact documentation RED head `d7e17676a76222a2e730b739275fd0afc0958700` then ran PostgreSQL 18.4 and 479 tests. Exactly two code-current documentation regressions failed: ADR 0054 still described the removed run-wide approval restriction/future multi-match persistence, and the `[Unreleased]` migration 0015 entry omitted the non-empty/equal allocation-side approval invariant. Coverage and package evidence did not become passing evidence for that RED head.

The distinct-source proposal repair is test-first lineage `18577f0ae24c0f45fd50a5630eab3175f98e6afa` → `91ee12f0719bbc902130c532db8b6f3bafb61e97`: the RED keeps exact monetary totals conserved while duplicating only immutable source identity, and the production descendant adds the narrow source-identity set guard. Hosted execution evidence for that descendant remains separate and is not inferred from source inspection.

The reviewed-decision distinct-source repair is ordinary descendant lineage `848309f21b216312127190a19582ee27a94c350d` → `0527478e7d439268b19bd66620dc20012361ac73`: the RED holds an otherwise valid explicit v2 decision constant while duplicating only one immutable `journal_reference`, includes a distinct two-journal positive control, and the production descendant rejects the repeated identity before it becomes reviewable decision evidence. The RED workflow and the repair workflow were still queued at the time this ADR evidence was updated; neither queued run is treated as hosted RED or GREEN evidence.

The decision source-identity domain repair is test-first lineage `089a0299eaa9ba91270db6cc68ce191e0b67fe11` → `693a89aad87a7ca4138d17af7874d772a990c760`: the RED varies only blank/non-string statement or journal source identities under otherwise valid match/abstain structures and keeps a valid distinct-v2 positive control. The production descendant validates the statement identity before decision branching and every matched journal identity before set-cardinality checks, so malformed values fail with domain-owned `ValueError` rather than becoming detached evidence or leaking a raw set/hash failure. Hosted execution evidence remains exact-head-specific and is not inferred from source inspection.

The decision review-guidance repair is test-first lineage `2ba2cfc83e4f436d707b16a92ec448d6aa8a2de8` → `7b2badc3481210fd9ee97fd97faf8f16e368dfff`: the RED holds valid identity, money, version, and match/abstain shapes constant while removing or corrupting only match `rule_code`, abstention rule semantics, or operator `next_action`. The production descendant requires non-empty match rule provenance, forbids rule provenance on abstentions, and requires a non-empty operator review instruction on every decision. Hosted execution evidence for these heads is not inferred from source inspection.

The source-reference identity-domain repair is test-first lineage `faa56752dd314bdd37adc309d34f531f177283fb` → `c9f9bcd22f3f3bc88f5a2a29cd1baccf05fd7e49`: the RED holds valid exact money, direction, dates, and matching semantics constant while varying only required or optional source-reference identity values. It also retains positive controls for valid strong-reference precedence and the all-optional-absent exact-money/date fallback. The production descendant validates required statement/journal identities and optional strong-reference identities at source-evidence construction before precedence or candidate comparison. Hosted execution evidence remains exact-head-specific and is not inferred from source inspection.

The source-currency domain repair is test-first lineage `9afc436f75403a1ba7c506f6f40dbaf6036bb8c6` → `7f09001af78ad55463ae815704bf544a15cc880d`: the RED holds exact money, CRDT direction, source identities, and dates constant while varying only malformed statement or book currency values, and keeps positive controls for valid strong-reference precedence and the all-optional-absent exact-money/date fallback. The production descendant performs an explicit string-type check and then delegates currency syntax to the accounting core `_require_currency`, preventing malformed equal values from entering match comparison without inventing a separate currency catalogue. Hosted execution evidence remains exact-head-specific and is not inferred from source inspection.

The source-date domain repair is test-first lineage `60c385200a6f00a341737a8418de445d13f5a6ef` → `cd8ac62d08d07fb04a2838e2ffc94b482933a3ae`: the RED keeps identity, exact money, canonical currency, CRDT direction, and valid matching structure fixed while varying only statement booking/value dates or book accounting dates across string, `datetime`, numeric, and `None` values. Valid-date controls preserve both provider-reference precedence and exact-money/bounded-date fallback. The production descendant validates source dates at evidence construction, before either rule can return a decision or perform date subtraction. Hosted execution evidence remains exact-head-specific and is not inferred from source inspection.

The source-direction domain repair is test-first lineage `99b929e0407d9c75182c8da52ed0ce00dc9060f3` → `7345235e58ee133d2dab06decf3e524b60662e41`: the RED holds source identity, exact money, canonical currency, calendar dates, and matching structure constant while varying only statement or book `credit_debit_code` across lowercase, blank, `None`, numeric, and unhashable collection values. Canonical CRDT/DBIT controls preserve strong-reference precedence and the exact-money/bounded-date fallback. The production descendant performs an explicit string-domain check before set membership so malformed direction evidence fails with domain-owned `ValueError` rather than a raw unhashable-membership `TypeError`. Hosted execution evidence remains exact-head-specific and is not inferred from source inspection.

The decision journal-population domain repair is ordinary test-first lineage `6fdf75bdbde8eaece86c1f785fd20d3bcf9f2c40` → `34fed7a175763e85f0afb38060a56563e7ff7b5d` → `5b00af864003263e1ec1520b518474bb360220a8`: the RED proves that v1, v2, and abstention decision shapes must reject mutable list populations while tuple controls remain valid. The production descendant adds the narrow tuple-domain guard before version/decision branching. A follow-up ordinary repair restores pre-existing reviewer-guidance punctuation that was unintentionally touched while writing the production file, leaving the current source diff against the parent limited to the intended four-line tuple guard. Hosted execution evidence remains exact-head-specific and is not inferred from source inspection.

Execution evidence belongs only to the exact head that produced it and is not transferred to later heads.

## References

See `docs/doctoring/REFERENCES.md` for APA 7 entries covering ISO 20022-1:2026, ISO 20022-4:2026, ISO 20022-9:2026, the ISO 20022 Registration Authority `camt.053.001.14` catalogue, and PostgreSQL 18 explicit locking used for the approval/allocation serialization boundary.