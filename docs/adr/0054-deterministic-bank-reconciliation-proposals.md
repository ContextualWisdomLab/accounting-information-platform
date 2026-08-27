# ADR 0054: Deterministic bank-reconciliation proposals

- Status: Proposed
- Date: 2026-08-26

## Context

The immutable `camt.053.001.14` bank-statement registry is an integrated accounting-information-platform fact. The next bounded reconciliation capability must compare that evidence with immutable posted-book evidence without allowing a statement line, a heuristic, or an LLM to write accounting facts.

ISO 20022 defines the interoperable message model and the Registration Authority currently publishes `camt.053.001.14` as `BankToCustomerStatementV14`. Those authorities define the source evidence vocabulary and schema; they do **not** prescribe this platform's journal-matching algorithm. The matching precedence and book-to-bank bridge below are therefore AIS control decisions, not claims of ISO conformance for reconciliation behavior. The Registration Authority catalogue was rechecked on 2026-08-26 and still lists `camt.053.001.14` in the Bank-to-Customer Cash Management message set last updated 19 March 2026.

## Decision

Add a pure proposal-only deterministic reconciliation boundary. It accepts normalized statement evidence, read-only posted-journal evidence, and a bounded date policy and returns either one reviewable proposal or an explicit abstention.

The first bounded precedence is:

1. a present provider reference;
2. otherwise a present end-to-end reference;
3. otherwise a present account-servicer reference;
4. only when no strong identity is present, exact amount, currency, CRDT/DBIT economic direction, and the configured booking/accounting-date window;
5. otherwise abstain.

A higher-confidence identity conflict never falls through to a weaker rule. A strong identity is not sufficient by itself: amount, currency, and credit/debit direction must also agree exactly. Duplicate candidates remain ambiguous even when money and direction agree. The weaker money/date rule is permitted only for one unique same-direction candidate. Reconciliation monetary evidence is canonical only when it is a finite, strictly positive `Decimal`; binary floating-point values, zero, negative values, `NaN`, and infinities fail before candidate comparison. CRDT/DBIT carries economic direction separately, so the engine never infers direction from a signed amount or coerces/rounds monetary evidence.

`DeterministicMatchPolicy.date_window_days` is runtime policy evidence, not a trusted Python type annotation. It must be a real non-negative integer. Boolean values, fractional values, and negative windows fail at policy construction before candidate comparison; `0` is the valid same-day-only policy.

Every abstention carries an exception code and an operator next action. The current bounded codes are `ambiguous_reference`, `amount_mismatch`, `currency_mismatch`, `direction_mismatch`, `date_window_mismatch`, and `no_candidate`.

Every returned decision, including an abstention, retains the immutable `statement_entry_reference` that produced it. This keeps a proposal attributable when it is logged, exported, or later persisted; caller context alone is not treated as durable audit provenance.

A `ReconciliationDecision` is itself fail-closed accounting evidence. A `match` must carry exactly one matched journal, a finite strictly positive exact `Decimal` allocation, and no exception code. An `abstain` must carry no matched journal, an exact zero `Decimal` allocation, and a non-empty exception code. Direct construction therefore cannot forge success-shaped close-review input that bypasses deterministic matching or fabricate exception evidence inconsistent with the decision type.

The decision object is a proposal only. It does not persist a reconciliation approval, mutate statement evidence, post or reverse a journal, select a chart account, or alter accounting policy. Any future adjustment must enter the existing accounting command boundary with its own idempotency identity, immutable source evidence, period/policy/authorization checks, and authoritative posting receipt.

### Exact book-to-bank bridge

A stacked successor adds a second pure projection over already selected immutable populations. It proves three equations independently with exact `Decimal` arithmetic:

1. `statement_opening_balance + statement_period_movements = statement_closing_balance`;
2. `book_opening_balance + posted_cash_book_movements = book_closing_balance`;
3. `reconciled_book_balance + outstanding_book_items - outstanding_bank_items = statement_closing_balance`.

Every monetary component presented to the bridge is runtime accounting evidence, not merely a Python annotation. Before any equation is evaluated, all nine monetary inputs must be finite `Decimal` values. Binary floating-point, `NaN`, and positive or negative infinity fail closed with a stable validation error. Finite zero and negative balances or movements remain valid because bridge populations can be signed; this boundary validates numeric representation and finiteness, not economic direction or positivity.

The bridge returns `statement_balance_mismatch`, `book_balance_mismatch`, or `bridge_difference` before it can return `reconciled`. There is no tolerance rounding: a one-minor-unit difference remains an explicit exception. Every result retains the reconciliation-run, immutable statement-population, and posted-book-population references and names the operator's next action. A bridge used for period-close review additionally carries immutable tenant, legal-entity, accounting-book, and bank-account-assignment references. A reconciled result is close evidence only; it is not a journal command or an approval.

### Buyer close-review projection

A further read-only projection may present the deterministic decisions and exact bridge to a controller without creating a new accounting authority. It exposes the bank closing balance, posted-book cash balance, reconciled balance, outstanding bank and book items, unexplained difference, deterministic match count, unresolved exception count and statement-entry references, and exact changes from a preceding bridge run. The projection carries the reconciliation-run, immutable statement-population, posted-book-population, tenant, legal-entity, accounting-book, and bank-account-assignment references so exported evidence remains attributable.

`Suitable for period-close review` requires more than a tying bridge and the absence of exceptions in a caller-selected list. The caller must supply the complete expected immutable statement-entry population for the run, and the decision population must contain exactly one decision for each expected statement entry: missing, duplicate, or extraneous decision identities fail closed before eligibility is computed. This prevents an unresolved statement line from disappearing merely because a caller omitted it from the projection input.

Close-review scope is not a caller assertion. The bridge result itself must carry non-empty tenant, legal-entity, accounting-book, and bank-account-assignment references, and the close-review scope must match those bridge-bound values exactly in addition to matching currency. An unbound bridge or a same-currency bridge from another accounting book or bank assignment fails closed before `suitable_for_period_close_review` is computed.

A preceding-run delta is permitted only when the current and preceding bridges are each bound to the same immutable tenant, legal entity, accounting book, bank-account assignment, and currency scope. A caller cannot relabel a foreign preceding bridge by supplying a current-scope object alongside it. The preceding bridge retains its own run and population identities; the scope comparison proves comparability without rewriting either immutable population.

`Suitable for period-close review` means only that the exact bridge is `reconciled`, the complete decision population has been proven, and that population contains no unresolved exception. It is not a reconciliation approval, period-close command, journal-posting permission, or accounting-policy decision. Any bridge difference or unresolved exception makes the projection fail closed and produces a customer-facing next action naming what must be resolved before close review is repeated.

JSON and CSV exports preserve monetary values as decimal strings and include the immutable close-review scope references. They do not convert exact accounting evidence to binary floating point or hide values behind presentation-only formatting. A preceding-run delta is informational comparison evidence; it never changes the immutable populations from which either bridge was computed.

### Allocation conservation

When a statement entry reconciles against several journal candidates, the split must be planned in exact `Decimal` and the allocations must sum exactly to the statement entry amount; the allocation module allocates each candidate's full amount and fails closed if the candidate total differs from the statement amount. When several statement entries reconcile to one journal total, the aggregate allocations must conserve the exact total on both sides; a book-side total that differs from the statement-side sum fails closed. Each `ReconciliationAllocation` is immutable, tenant- and run-scoped, carries the statement and journal reference plus currency, and rejects non-exact or non-positive money. Allocation plans are still evidence: they never post, reverse, approve, or adjust a journal, and a later relational slice persists them with concurrency and double-consumption controls.

### Allocation persistence

Migration 0014 persists the planned evidence relationally: `reconciliation_candidate` rows carry statement/journal references and exact statement and journal amounts; `reconciliation_match` records the single approved evaluation per run with `proposed`, `approved`, `rejected`, or `superseded` status; `statement_match_allocation` and `journal_match_allocation` keep exact positive amounts with statement and journal identity. A partial unique index (`reconciliation_match_approved_single`, `WHERE match_status_code = 'approved'`) enforces at most one approved match per run at the database boundary so a fully consumed source amount cannot be reused by a second active match. All four tables are forced-row-level-security tenant-scoped. Persisted matches and allocations still carry no journal-posting, reversal, or adjustment authority.

### Durable approval evidence

A persisted reconciliation match is not human-approved merely because application code writes `match_status_code='approved'`. Migration 0016 records one immutable `reconciliation_approval` control fact per tenant/run/match with a tenant-scoped command key, immutable source hash, approver reference, purpose, decision, effective time, and recorded time. PostgreSQL permits the proposed-to-approved transition only after an `approved` control fact exists. A rejected approval is terminal for that match; corrections create a new reviewed match/control fact rather than rewriting approval history. Approval evidence remains non-posting authority and cannot post, reverse, close, choose accounting policy, or mutate source statement/journal facts.

## Consequences and limits

The statement-side direction is the normalized ISO 20022 `CdtDbtInd` evidence already retained by the integrated statement registry. Both statement-side and book-side reconciliation evidence reject any direction code other than `CRDT` or `DBIT` before matching begins, so two equally invalid arbitrary strings can never become a match. They also reject non-`Decimal`, non-finite, zero, or negative monetary evidence before matching; direction stays a separate CRDT/DBIT fact rather than being encoded as a sign. Book candidates must expose the corresponding economic cash-movement direction explicitly; amount/reference equality alone cannot reconcile an incoming bank credit to an outgoing book movement or vice versa. A direction conflict fails closed before a proposal is emitted.

The bounded date window is fail-closed configuration. A malformed window cannot be interpreted as an unusual matching policy and cannot silently turn otherwise eligible evidence into a success- or exception-shaped decision. Operators must supply zero or a whole non-negative number of days before reconciliation begins.

This slice still does not claim the complete issue #8 reconciliation vertical. Persistence of immutable reconciliation runs/candidates, many-to-many allocation conservation, explicit approval/exception records, concurrency protection, temporal knowledge cutoffs, close-package integration, and durable approval evidence remain later bounded work and must be test-first before they can be treated as integrated capability. The close-review projection is a read model/export surface over current immutable evidence, not persistence for those missing controls.

LLM or probabilistic output may later summarize or prioritize an exception, but it cannot invoke a proposal, bridge result, or close-review projection as an approval, consume monetary evidence, or post an adjustment.

## Evidence

The initial RED contract was executed on exact PR head `80ce0eb1cffb4b60199d22ff20830abc985bc7d3`: PostgreSQL 18.4 foundation behavior ran, then all six initial reconciliation tests failed at the same first causal boundary because `accounting_information_platform.reconciliation` did not exist. The production implementation was added only after that observed RED boundary. Later exact head `d7a96af21698a15f8722f63bc51b76fdb12c56de` added the CRDT/DBIT regression contract after comparing this proposal model with the integrated normalized statement model.

Exact predecessor `73349aeb6973fa26fa97fe7c8f132aa79ced0aca` then failed Accounting Foundation CI `32973939075` at behavior/repository tests after exact-head SAST/security/dependency jobs succeeded; all later coverage/package evidence was skipped. The bounded defect was missing source-statement provenance on `ReconciliationDecision`. The narrow repair makes `statement_entry_reference` mandatory on both match and abstention results. Predecessor execution evidence does not transfer to later heads.

Exact RED head `5814713d55ef2e5aa7d930fde39f1dbf9ad24d17` then ran 436 PostgreSQL-backed behavior/repository tests in Accounting Foundation CI `33054370667`. Seven direct-construction regressions failed because `ReconciliationDecision` accepted forged success- or exception-shaped evidence without raising `ValueError`; exact-head SAST, security, and live-base dependency/OSV jobs succeeded, while later coverage, repository-validation, compilation, and package/SBOM/provenance evidence was skipped after the behavior failure. The narrow repair validates decision structure at construction and does not change matching precedence or grant posting/approval authority.

The exact bridge RED contract was executed on head `20af44c663a33a63cb002725fdba8dab9bc83cd3` with PostgreSQL 18.4. Existing reconciliation and foundation behavior passed; all three bridge tests failed at the first causal boundary because `accounting_information_platform.reconciliation_bridge` did not exist, and coverage/package stages were correctly skipped. The narrow successor implementation therefore adds only the pure projection required by those observed failures.

The close-review RED contract was executed on exact head `051fb233d0af04c6b6208c23b341e05c236bd200` in Accounting Foundation CI `33028878208`. PostgreSQL 18.4 initialized and the existing behavior passed until all four new close-review tests reached the same first causal boundary: `accounting_information_platform.reconciliation_read_model` did not exist. Coverage, repository-validation, and package evidence correctly did not become passing evidence for that RED head. The narrow successor adds only the read model and exact-value JSON/CSV exports required by that observed boundary.

Exact RED head `b1dc468dc42591a4d92dc96957dc905e036d12c1` then ran 404 PostgreSQL-backed behavior/repository tests and failed six new monetary-domain cases before coverage or package evidence could run: binary float, zero, negative, `NaN`, positive infinity, and negative infinity were all accepted by the typed evidence constructors because type annotations were not runtime authority. The narrow repair at `b2fdb5cdaa24e4735e5961e29f0b310bb8560349` rejects every value except a finite, strictly positive `Decimal` at the evidence boundary before matching. Execution evidence belongs only to the exact head that produced it and is not transferred to later documentation heads.

Exact RED head `32eb1c43295967909b2d5835ef6d37a025705a78` then ran 408 PostgreSQL-backed behavior/repository tests and failed exactly the three new invalid-policy cases: boolean, fractional, and negative `date_window_days` values were accepted because the frozen dataclass had no runtime policy-domain validation. The same-day `0` case passed. Coverage, repository-validation, compilation, and package/SBOM/provenance steps were skipped on that RED head. The narrow repair adds construction-time validation only; it does not alter matching precedence, monetary comparison, journal authority, or statement immutability.

Exact bridge-domain RED head `4277f5c5c6c88157fd5e4513c1d58ca2bcc2ef28` then ran 418 PostgreSQL-backed behavior/repository tests. The binary-float case reached bridge arithmetic and escaped as raw `TypeError`, while all 27 non-finite Decimal subcases failed to raise the required stable validation error; the finite negative-balance control passed. Coverage, repository validation, compilation, and package/SBOM/provenance correctly did not run after the RED behavior failure. The narrow repair validates the nine bridge monetary components as finite `Decimal` values before any equation while continuing to allow signed finite balances and movements.

Exact close-review scope RED head `26c29d6452a1f00cf4b890f6f868023944711fb4` then ran 430 PostgreSQL-backed tests. Existing close-review, reconciliation, bridge, posting, security, and repository behavior passed, while the four new completeness/scope tests failed at the first causal boundary because `ReconciliationCloseReviewScope` did not exist. Later coverage, repository-validation, compilation, and package/SBOM/provenance evidence was skipped. The narrow repair adds explicit immutable close-review scope identity, proves one decision per expected statement entry, and permits historical deltas only for the same tenant/entity/book/bank-account/currency scope.

Exact bridge-scope RED head `11173b2bfead093586f2fc3237ed0f6708669048` then ran 439 PostgreSQL-backed tests in Accounting Foundation CI `33061381625`. Exactly two new regressions failed because a same-currency current or preceding bridge could be paired with caller-supplied accounting/bank scope without raising `ValueError`; exact-head SAST, security, and live-base dependency/OSV jobs succeeded, while coverage, repository-validation, compilation, and package/SBOM/provenance were skipped. The narrow repair binds tenant/entity/book/bank-account assignment identity into bridge evidence and rejects unbound or relabelled bridge scope before close-review eligibility or historical deltas are computed.

## References

See `docs/doctoring/REFERENCES.md` for APA 7 entries covering ISO 20022-1:2026, ISO 20022-4:2026, ISO 20022-9:2026, and the ISO 20022 Registration Authority `camt.053.001.14` catalogue.
