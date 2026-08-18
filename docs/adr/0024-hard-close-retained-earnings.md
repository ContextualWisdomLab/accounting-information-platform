# ADR 0024: Hard-close parks period earnings in retained earnings

**Status:** Accepted

## Decision

`PostgresPostingLedger.close_fiscal_period` / `accept_period_close` / `POST /period-closes` keep the ADR 0023 two-step command. On `hard_closed` only, AIS posts one AIS-owned closing journal in the same transaction, before the trial-balance snapshot. Soft-close writes zero closing journals.

The catalog seed adds chart account `310100` (`account_class_code=equity`, `normal_balance_code=credit`) and `account_role_code=retained_earnings` → `310100`, the same mapping pattern as `tax_payable` → `210100`. Ordinary Billing proposals may not use `retained_earnings`; that role is reserved for this close journal. The published Billing proposal schema forbids `account_role_code=retained_earnings` (`not: {const: "retained_earnings"}`) so a commercial payload is schema-invalid before ingest. `ingest_journal_proposal` and `POST /journal-proposals` still reject that role as HTTP 422 before a journal is written. Period-close retained-earnings journals stay AIS-authored (`POST /period-closes` is the only writer of 310100). Catalog roles stay the existing seven; this decision does not invent a withholding role.

The closing journal zeros catalog `usage_revenue` 410100 and `write_off_expense` 510100 only: debit 410100 / credit `retained_earnings` 310100 for net revenue, and debit 310100 / credit 510100 for net expense. The exact remainder lands on 310100 so post-close trial-balance equity ties. AIS does not invent another chart account. If those income-statement roles are already net zero, AIS writes zero closing journals. If the two nets offset to zero income, AIS still posts the clearing lines and omits the 310100 plug. Hard-close loads the period-close package in the same REPEATABLE READ transaction first and fails closed when that binder cannot be loaded or the trial balance does not balance. Leftover cash on 210200 may stay non-zero. Re-hard-close of the same period-close `idempotency_key` replays the existing close receipt and does not post a second closing journal. A different key on an already-locked period fails closed.

`GET /financial-statements?statement_type_code=income_statement` excludes AIS closing journals (`journal_reference` prefix `urn:cwl:accounting:general_journal:period_closing:`) so period earnings match the pre-close profit or loss. `GET /financial-statements?statement_type_code=balance_sheet` after hard-close includes `310100` and returns `net_income_amount` `0` because those earnings now sit in equity. `GET /trial-balances` after hard-close is the snapshot that includes the closing journal.

IAS 1 requires a statement of financial position that presents equity separately from profit or loss for the period (IFRS Foundation, 2022). The closing process transfers that period result into equity so the next period’s sheet does not carry a floating earnings plug.

## Consequences

Controllers can hard-close once and read a balance sheet that ties without `net_income_amount` as a plug. Income-statement inquiry still shows the period’s revenue and expense activity. Soft-close remains the adjusting window from ADR 0023 and does not park earnings.
