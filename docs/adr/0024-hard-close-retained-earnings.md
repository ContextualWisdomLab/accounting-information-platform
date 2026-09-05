# ADR 0024: Hard-close parks period earnings in retained earnings

**Status:** Accepted

## Decision

`PostgresPostingLedger.close_fiscal_period` / `accept_period_close` / `POST /period-closes` keep the ADR 0023 two-step command. On `hard_closed` only, AIS posts one AIS-owned closing journal in the same transaction, before the trial-balance snapshot. Soft-close writes zero closing journals.

The catalog seed adds chart account `310100` (`account_class_code=equity`, `normal_balance_code=credit`) and `account_role_code=retained_earnings` → `310100`, the same mapping pattern as `tax_payable` → `210100`. Ordinary Billing proposals may not use `retained_earnings`; that role is reserved for this close journal. The published Billing proposal schema forbids `account_role_code=retained_earnings` (`not: {const: "retained_earnings"}`) so a commercial payload is schema-invalid before ingest. `ingest_journal_proposal` and `POST /journal-proposals` still reject that role as HTTP 422 before a journal is written. Period-close retained-earnings journals stay AIS-authored (`POST /period-closes` is the only writer of 310100). Catalog roles stay the existing seven; this decision does not invent a withholding role.

The closing journal zeros catalog `usage_revenue` 410100 and `write_off_expense` 510100 only: debit 410100 / credit `retained_earnings` 310100 for net revenue, and debit 310100 / credit 510100 for net expense. The exact remainder lands on 310100 so post-close trial-balance equity ties. AIS does not invent another chart account. If those income-statement roles are already net zero, AIS writes zero closing journals. If the two nets offset to zero income, AIS still posts the clearing lines and omits the 310100 plug. Hard-close loads the period-close package in the same REPEATABLE READ transaction first and fails closed when that binder cannot be loaded or the trial balance does not balance. Leftover cash on 210200 may stay non-zero. Re-hard-close of the same period-close `idempotency_key` replays the existing close receipt and does not post a second closing journal. A different key on an already-locked period fails closed.

`GET /financial-statements?statement_type_code=income_statement` excludes AIS closing journals (`journal_reference` prefix `urn:cwl:accounting:general_journal:period_closing:`) so period earnings match the pre-close profit or loss. `GET /financial-statements?statement_type_code=balance_sheet` after hard-close includes `310100` and returns `net_income_amount` `0` because those earnings now sit in equity. `GET /trial-balances` after hard-close is the snapshot that includes the closing journal.

IAS 1 requires a statement of financial position that presents equity separately from profit or loss for the period (IFRS Foundation, 2022). The closing process transfers that period result into equity so the next period’s sheet does not carry a floating earnings plug.

## Proposed amendment on PR #53: posted-role temporal authority

The accepted retained-earnings design does not authorize a later Accounting Policy catalog change to rewrite an already-posted journal fact. For historical P&L source classification at hard close, `journal_entry_line.account_role_code` is the authority because it was persisted with the immutable posted line. `account_role_mapping` remains the effective-dated policy authority while a new proposal is resolved, and the current `retained_earnings` mapping remains the close-time authority for the destination of the newly created AIS closing journal.

Accordingly, `_post_closing_journal()` must select, filter, and group historical `usage_revenue` / `write_off_expense` from the persisted journal-line role and must not join the current effective mapping merely to reconstruct that historical classification. Expiring or superseding a mapping after posting must neither suppress the posted P&L population nor reclassify it at hard close. This amendment is Proposed until PR #53 reaches its protected integration gates; it does not change this ADR's previously accepted retained-earnings ownership or the posting-time policy resolver.

The executable acceptance is `tests/test_postgres_period_close_posted_role_stability_red.py` plus `tests/test_period_close_posted_role_source_contract.py`. The detailed RED→candidate lineage, alternatives, scope-preservation evidence, and rollback boundary are maintained in `docs/doctoring/PERIOD_CLOSE_POSTED_ROLE_TRACEABILITY.md`.

This temporal-authority split is an AIP DDD/audit-evidence control. IAS 1 does not prescribe the PostgreSQL column, join shape, or effective-dated implementation used to enforce it.

## Consequences

Controllers can hard-close once and read a balance sheet that ties without `net_income_amount` as a plug. Income-statement inquiry still shows the period’s revenue and expense activity. Soft-close remains the adjusting window from ADR 0023 and does not park earnings.

For the Proposed PR #53 amendment, controllers can also change future account-role policy without changing the close semantics of immutable historical journal lines. Reporting projections that still infer historical semantics only from current mappings remain a separate Reporting-Export repair and must not create a second Period Close authority.
