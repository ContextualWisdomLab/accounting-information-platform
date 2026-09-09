# ADR 0019: HTTP account-ledger inquiry

**Status:** Accepted historical contract; accounting-book scope amendment Proposed in #57

## Historical decision

AIS exposes `lookup_account_ledger` and `GET /account-ledgers?legal_entity_reference=&chart_account_code=` on the same stdlib HTTP surface as posted-journal inquiry. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The accepted implementation reads existing `journal_entry_line` rows for that tenant, legal entity, and `chart_account_code`, joined through `general_journal`, `chart_account`, `legal_entity_record`, and optional `fiscal_period`. Line keys copy `GET /journals` (`line_number`, `chart_account_code`, `account_role_code`, `debit_amount`, `credit_amount`) and add `journal_reference` and `posted_at` from the journal header. `period_debit_total` and `period_credit_total` are exact decimal strings for the full filtered scope, not only the current page. Optional `fiscal_period_reference` keeps lines whose journal is in that period. An empty activity set returns `ledger_lines: []` rather than 404. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `posted_at|journal_reference|line_number`. `POST /account-ledgers` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

That historical contract omitted accounting-book identity. It remains a record of the protected implementation that produced the current defect and is not rewritten as though book scope had always existed.

## Review finding

A legal entity may own multiple accounting books, and `chart_account_code` is book-scoped. A statutory book and a management book can therefore legitimately reuse `110100` while retaining different `chart_account` entities and different authoritative journal populations.

Real PostgreSQL evidence in #57 demonstrates the consequence. On exact `7b22dde45af55d6402cfc99be3c95751b6b622ce`, Accounting Foundation `34303435636` reached the two-book regression and returned statutory `period_debit_total = 50000.000000` instead of the statutory-only `25000`. The current read predicates do not constrain `general_journal.accounting_book_id`, so sibling-book facts can be represented as one ledger. Successor REDs also require the HTTP route to reject a missing book and the public library inquiry to accept explicit book identity.

This is a General Ledger read-model authority defect. It does not alter immutable posted facts, Posting, Period Close, Reconciliation, Accounting Policy, Billing commercial truth, or the ownership of `general_journal`, `journal_entry_line`, `chart_account`, and `accounting_book`.

## Proposed amendment

The repaired account-ledger contract will require one explicit accounting book throughout the inquiry:

- `lookup_account_ledger` requires `book_reference` and validates it before reading ledger facts;
- HTTP requires `book_reference`, while `accounting_book_reference` is accepted only as the equivalent product/API spelling rather than as a second identity;
- the repository resolves exactly one active `accounting_book` under the requested tenant and legal entity;
- both the paged line population and the full-scope debit/credit totals constrain `general_journal.accounting_book_id` to that resolved book;
- optional fiscal-period filtering composes inside the same book scope and never substitutes for it;
- a valid book with no matching activity returns `ledger_lines: []` and exact zero totals;
- missing, unknown, tenant-mismatched, or cross-entity book scope fails closed without exposing sibling existence;
- cursor continuation remains inside the same tenant/legal-entity/book/account/period population;
- exact Decimal serialization, immutable journal facts, page limits, and read-only semantics remain unchanged.

## Alternatives rejected

Inferring a default book from `book_role_code`, a current account-role mapping, a reused account code, or the first matching catalog row is rejected because those are mutable or ambiguous selectors and would make the read result depend on catalog state rather than explicit ledger identity. Keeping the cross-book contract is rejected because totals and pagination can combine legally distinct books. Creating a Reporting- or Period-Close-owned projection to hide the defect is rejected because General Ledger owns this read model and the authoritative facts already exist in AIS persistence.

## Implementation and evidence boundary

The amendment is **Proposed**, not Accepted, until one unchanged successor head implements the complete book-scoped read and passes the focused two-book PostgreSQL regression, HTTP/library admission tests, full real-PostgreSQL behavior suite, 100% owned production statement/branch/docstring coverage, repository contracts, Security/SAST/dependency checks, reproducible package/SBOM/provenance, current review gates, and normal protected integration.

`src/accounting_information_platform/persistence.py` is concurrently mutable in Period Close #53. The General Ledger repair must not overwrite or duplicate that work. If #53 produces a descendant first, #57 must read and preserve the intervening delta before applying the book-scoped persistence change; no force-push or destructive rebase is permitted.

Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain #37 single-writer surfaces and are updated from protected integrated evidence rather than this mutable proposal branch.
