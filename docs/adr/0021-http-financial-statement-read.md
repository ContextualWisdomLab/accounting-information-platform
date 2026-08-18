# ADR 0021: HTTP financial-statement read

**Status:** Accepted

## Decision

`accounting_core.chart_account` now stores a durable `account_class_code` constrained to `asset`, `liability`, `equity`, `revenue`, or `expense`. Those five classes are the IAS 1 presentation split of a statement of financial position versus a statement of profit or loss (IFRS Foundation, 2022). `normal_balance_code` remains the debit/credit side and does not classify statements.

AIS exposes `lookup_financial_statement` and `GET /financial-statements?legal_entity_reference=&book_reference=&fiscal_period_reference=&statement_type_code=` on the same stdlib HTTP surface as trial-balance read. Optional prior-period comparison stays on that route (ADR 0025). `statement_type_code` is exactly `income_statement` or `balance_sheet`. Amounts come from the same trial-balance aggregation already used by `GET /trial-balances` (live posted lines, or the close snapshot when the period is hard-closed). Income-statement lines are `revenue` and `expense` and exclude AIS period-closing journals so period earnings match the pre-close profit or loss (ADR 0024). Balance-sheet lines are `asset`, `liability`, and `equity`. Before hard-close `net_income_amount` is credit-normal earnings (revenue − expense) on both statements so the sheet can tie: assets = liabilities + equity + net income. After hard-close those earnings sit in 310100 and the sheet `net_income_amount` is 0. Empty books return `statement_lines` [] and zero totals. Missing catalog or period facts fail closed. `POST /financial-statements` is 405. A tenant-header mismatch is rejected before the read and writes zero rows. `GET /chart-accounts` now returns `account_class_code` on each existing row.

## Consequences

Controllers can produce an income statement and a balance sheet from posted books without SQL and without a second numerical truth. Chart classification is an authoritative catalog fact. This decision is numbered 0021 because ADR 0020 recorded the catalog read while classification was still missing.
