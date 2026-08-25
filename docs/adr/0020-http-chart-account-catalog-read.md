# ADR 0020: HTTP chart-account catalog read

**Status:** Accepted

## Decision

`accounting_core.chart_account` stores `chart_account_code`, `account_name`, and `normal_balance_code` (`debit` or `credit`). It has no `account_class_code` or other durable split of asset / liability / equity versus revenue / expense. `normal_balance_code` cannot classify an income statement versus a balance sheet: assets and expenses are both debit-normal; liabilities and revenue are both credit-normal. This slice does not invent a class column and does not add a migration.

AIS therefore exposes `lookup_chart_accounts` and `GET /chart-accounts?legal_entity_reference=&book_reference=` as the catalog sibling of ADR 0013 mapping read. Chart accounts are keyed by tenant and book. The read returns existing rows only. An empty book returns `chart_accounts` []. Missing legal entity or book fails closed. `POST /chart-accounts` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

`GET /financial-statements` waits on a durable `account_class_code` (or equivalent) on `chart_account`. Until that classification exists, AIS will not project income-statement or balance-sheet lines from trial-balance totals.

## Consequences

Controllers can inspect the statutory chart without SQL. Financial-statement production remains blocked until classification is an authoritative catalog fact.
