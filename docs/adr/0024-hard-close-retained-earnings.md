# ADR 0024: Hard-close parks period earnings in retained earnings

**Status:** Accepted

## Decision

`PostgresPostingLedger.close_fiscal_period` / `accept_period_close` / `POST /period-closes` keep the ADR 0023 two-step command. On `hard_closed` only, AIS posts one AIS-owned closing journal in the same transaction, before the trial-balance snapshot. Soft-close writes zero closing journals.

The catalog seed adds chart account `310100` (`account_class_code=equity`, `normal_balance_code=credit`) and `account_role_code=retained_earnings` → `310100`, the same mapping pattern as `tax_payable` → `210100`. Ordinary Billing proposals may not use `retained_earnings`; that role is reserved for this close journal. `ingest_journal_proposal` and `POST /journal-proposals` reject Billing `account_role_code=retained_earnings` as HTTP 422 before a journal is written. Period-close retained-earnings journals stay AIS-authored.

The closing journal zeros each revenue and expense account that still has a non-zero credit-net or debit-net, and plugs the exact remainder to `310100` so the journal balances. If income-statement accounts are already net zero, AIS writes zero closing journals. If revenue and expense nets offset to zero income, AIS still posts the clearing lines and omits the `310100` plug. Re-hard-close replays the existing close receipt and does not post a second closing journal.

`GET /financial-statements?statement_type_code=income_statement` excludes AIS closing journals (`journal_reference` prefix `urn:cwl:accounting:general_journal:period_closing:`) so period earnings match the pre-close profit or loss. `GET /financial-statements?statement_type_code=balance_sheet` after hard-close includes `310100` and returns `net_income_amount` `0` because those earnings now sit in equity. `GET /trial-balances` after hard-close is the snapshot that includes the closing journal.

IAS 1 requires a statement of financial position that presents equity separately from profit or loss for the period (IFRS Foundation, 2022). The closing process transfers that period result into equity so the next period’s sheet does not carry a floating earnings plug.

## Consequences

Controllers can hard-close once and read a balance sheet that ties without `net_income_amount` as a plug. Income-statement inquiry still shows the period’s revenue and expense activity. Soft-close remains the adjusting window from ADR 0023 and does not park earnings.
