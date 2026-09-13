# ADR 0019: HTTP account-ledger inquiry

**Status:** Accepted historical contract; accounting-book scope amendment Proposed in #57

## Historical decision

AIS exposes `lookup_account_ledger` and `GET /account-ledgers?legal_entity_reference=&chart_account_code=` on the same stdlib HTTP surface as posted-journal inquiry. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The accepted implementation reads existing `journal_entry_line` rows for that tenant, legal entity, and `chart_account_code`, joined through `general_journal`, `chart_account`, `legal_entity_record`, and optional `fiscal_period`. Line keys copy `GET /journals` (`line_number`, `chart_account_code`, `account_role_code`, `debit_amount`, `credit_amount`) and add `journal_reference` and `posted_at` from the journal header. `period_debit_total` and `period_credit_total` are exact decimal strings for the full filtered scope, not only the current page. Optional `fiscal_period_reference` keeps lines whose journal is in that period. An empty activity set returns `ledger_lines: []` rather than 404. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `posted_at|journal_reference|line_number`. `POST /account-ledgers` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

That historical contract omitted accounting-book identity. It remains a record of the protected implementation that produced the current defect and is not rewritten as though book scope had always existed.

## Review finding

A legal entity may own multiple accounting books, and `chart_account_code` is book-scoped. A statutory book and a management book can therefore legitimately reuse `110100` while retaining different `chart_account` entities and different authoritative journal populations.

Real PostgreSQL evidence in #57 demonstrates the consequence. On exact `7b22dde45af55d6402cfc99be3c95751b6b622ce`, Accounting Foundation `34303435636` reached the two-book regression and returned statutory `period_debit_total = 50000.000000` instead of the statutory-only `25000`. The current persistence predicates do not constrain `general_journal.accounting_book_id`, so sibling-book facts can be represented as one ledger.

This is a General Ledger read-model authority defect. It does not alter immutable posted facts, Posting, Period Close, Reconciliation, Accounting Policy, Billing commercial truth, or the ownership of `general_journal`, `journal_entry_line`, `chart_account`, and `accounting_book`.

## Proposed amendment

The repaired account-ledger contract requires one explicit accounting book throughout the inquiry:

- `lookup_account_ledger` requires canonical `book_reference` and validates it before constructing the PostgreSQL ledger adapter;
- HTTP requires one logical book identity; canonical query spelling is `book_reference`, while `accounting_book_reference` is only an equivalent HTTP alias;
- missing, blank, non-CWL-URN, conflicting alias values, or repeated distinct canonical values fail closed rather than selecting first/last by query order;
- the persistence resolver must select exactly one Accounting Book Entity under the requested tenant, legal entity, durable `book_reference`, and an explicit accounting-effective instant;
- effective-time membership is half-open: `valid_from <= effective_at AND (valid_to IS NULL OR valid_to > effective_at)`;
- resolution must fail closed on 0 or more than 1 effective matches and must not substitute `.fetchone()`, implicit row order, `valid_to IS NULL`, `book_role_code`, current account-role mappings, or account-code inference for identity;
- the durable `(tenant, legal entity, book_reference)` catalog prerequisite is database-owned temporal non-overlap, including concurrent writers, as owned by #58 / Draft #59; this PR consumes that protected contract after integration rather than copying mutable migration/source bytes;
- both the paged line population and the full-scope debit/credit totals constrain `general_journal.accounting_book_id` to the same resolved book;
- optional fiscal-period filtering composes inside the same book scope and never substitutes for it;
- a valid book with no matching activity returns `ledger_lines: []` and exact zero totals;
- unknown, tenant-mismatched, cross-entity, or ambiguous book scope fails closed without exposing sibling existence;
- cursor continuation remains inside the same tenant/legal-entity/book/account/period population;
- exact Decimal serialization, immutable journal facts, page limits, and read-only semantics remain unchanged.

The effective instant is domain input, not database/system time chosen implicitly by the resolver. The persistence implementation must derive the inquiry's accounting-effective instant from the normally integrated accounting contract that owns that semantic. ADR 0019 does not create a competing Accounting Book temporal authority.

## Alternatives rejected

Inferring a default book from `book_role_code`, a current account-role mapping, a reused account code, the current system time, an open-ended `valid_to IS NULL` row, or the first matching catalog row is rejected because those are mutable, incomplete, or ambiguous selectors and would make the read result depend on incidental catalog/query state rather than explicit ledger identity. Keeping the cross-book contract is rejected because totals and pagination can combine legally distinct books. Creating a Reporting- or Period-Close-owned projection to hide the defect is rejected because General Ledger owns this read model and the authoritative facts already exist in AIS persistence.

## Current implementation state

The non-persistence admission slice is implemented on the #57 ordinary lineage through `12496ebed6a52d1a09c5593eeb658f7b8d1eec9d`:

- `lookup_account_ledger(...)` exposes keyword-only canonical `book_reference`, validates the opaque CWL URN, and does not construct `PostgresPostingLedger` when book admission fails;
- `/account-ledgers` retains blank values, admits canonical/alias spellings only when every supplied value identifies the same logical book, and otherwise returns HTTP 400 with `book_reference` named;
- the real PostgreSQL regression preserves exact six-decimal ledger values rather than weakening monetary assertions.

This is only partial GREEN. Current persistence still ignores the admitted `book_reference`, so the real same-legal-entity/two-book/same-account-code population/totals regression remains intentionally RED. Accounting Foundation `34735378618` on exact `12496eb...` reached `Run behavior and repository tests` and failed there; exact-head security, SAST, and dependency-diff jobs were GREEN, while later coverage/package stages were skipped. No merge or release conclusion follows from the admission repair alone.

## Implementation and evidence boundary

The amendment is **Proposed**, not Accepted, until one unchanged successor head implements the complete book-scoped read and passes the focused two-book PostgreSQL regression, HTTP/library admission tests, full real-PostgreSQL behavior suite, 100% owned production statement/branch/docstring coverage, repository contracts, Security/SAST/dependency checks, reproducible package/SBOM/provenance, current review gates, and normal protected integration.

`src/accounting_information_platform/persistence.py` is concurrently mutable in Period Close #53. The General Ledger repair must not overwrite or duplicate that work. The live dependency stack is `#29 -> #47 -> #53`; after #47 normally integrates and #53 is non-force restacked/reconciled, the persistence phase must read every intervening delta and consume the protected #58/#59 Accounting Book temporal invariant/resolver contract. No force-push, destructive rebase, mutable source copy, or parallel `persistence.py` writer is permitted.

Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain #37 single-writer surfaces and are updated from protected integrated evidence rather than this mutable proposal branch.
