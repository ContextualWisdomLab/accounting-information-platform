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

A higher-confidence identity conflict never falls through to a weaker rule. A strong identity is not sufficient by itself: amount, currency, and credit/debit direction must also agree exactly. Duplicate candidates remain ambiguous even when money and direction agree. The weaker money/date rule is permitted only for one unique same-direction candidate. All monetary values remain `Decimal`; the engine does not round, coerce, or consume evidence on abstention.

Every abstention carries an exception code and an operator next action. The current bounded codes are `ambiguous_reference`, `amount_mismatch`, `currency_mismatch`, `direction_mismatch`, `date_window_mismatch`, and `no_candidate`.

Every returned decision, including an abstention, retains the immutable `statement_entry_reference` that produced it. This keeps a proposal attributable when it is logged, exported, or later persisted; caller context alone is not treated as durable audit provenance.

The decision object is a proposal only. It does not persist a reconciliation approval, mutate statement evidence, post or reverse a journal, select a chart account, or alter accounting policy. Any future adjustment must enter the existing accounting command boundary with its own idempotency identity, immutable source evidence, period/policy/authorization checks, and authoritative posting receipt.

### Exact book-to-bank bridge

A stacked successor adds a second pure projection over already selected immutable populations. It proves three equations independently with exact `Decimal` arithmetic:

1. `statement_opening_balance + statement_period_movements = statement_closing_balance`;
2. `book_opening_balance + posted_cash_book_movements = book_closing_balance`;
3. `reconciled_book_balance + outstanding_book_items - outstanding_bank_items = statement_closing_balance`.

The bridge returns `statement_balance_mismatch`, `book_balance_mismatch`, or `bridge_difference` before it can return `reconciled`. There is no tolerance rounding: a one-minor-unit difference remains an explicit exception. Every result retains the reconciliation-run, immutable statement-population, and posted-book-population references and names the operator's next action. A reconciled result is close evidence only; it is not a journal command or an approval.

## Consequences and limits

The statement-side direction is the normalized ISO 20022 `CdtDbtInd` evidence already retained by the integrated statement registry. Both statement-side and book-side reconciliation evidence reject any direction code other than `CRDT` or `DBIT` before matching begins, so two equally invalid arbitrary strings can never become a match. Book candidates must expose the corresponding economic cash-movement direction explicitly; amount/reference equality alone cannot reconcile an incoming bank credit to an outgoing book movement or vice versa. A direction conflict fails closed before a proposal is emitted.

This slice still does not claim the complete issue #8 reconciliation vertical. Persistence of immutable reconciliation runs/candidates, many-to-many allocation conservation, explicit approval/exception records, concurrency protection, temporal knowledge cutoffs, close-package integration, and exports remain later bounded work and must be test-first before they can be treated as integrated capability.

LLM or probabilistic output may later summarize or prioritize an exception, but it cannot invoke a proposal or bridge result as an approval, consume monetary evidence, or post an adjustment.

## Evidence

The initial RED contract was executed on exact PR head `80ce0eb1cffb4b60199d22ff20830abc985bc7d3`: PostgreSQL 18.4 foundation behavior ran, then all six initial reconciliation tests failed at the same first causal boundary because `accounting_information_platform.reconciliation` did not exist. The production implementation was added only after that observed RED boundary. Later exact head `d7a96af21698a15f8722f63bc51b76fdb12c56de` added the CRDT/DBIT regression contract after comparing this proposal model with the integrated normalized statement model.

Exact predecessor `73349aeb6973fa26fa97fe7c8f132aa79ced0aca` then failed Accounting Foundation CI `32973939075` at behavior/repository tests after exact-head SAST/security/dependency jobs succeeded; all later coverage/package evidence was skipped. The bounded defect was missing source-statement provenance on `ReconciliationDecision`. The narrow repair makes `statement_entry_reference` mandatory on both match and abstention results. Predecessor execution evidence does not transfer to later heads.

The exact bridge RED contract was executed on head `20af44c663a33a63cb002725fdba8dab9bc83cd3` with PostgreSQL 18.4. Existing reconciliation and foundation behavior passed; all three bridge tests failed at the first causal boundary because `accounting_information_platform.reconciliation_bridge` did not exist, and coverage/package stages were correctly skipped. The narrow successor implementation therefore adds only the pure projection required by those observed failures.

## References

See `docs/doctoring/REFERENCES.md` for APA 7 entries covering ISO 20022-1:2026, ISO 20022-4:2026, ISO 20022-9:2026, and the ISO 20022 Registration Authority `camt.053.001.14` catalogue.
