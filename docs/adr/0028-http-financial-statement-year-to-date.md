# ADR 0028: HTTP financial-statement year-to-date scope

**Status:** Accepted

## Decision

`lookup_financial_statement`, `load_financial_statement`, and `GET /financial-statements` keep the ADR 0021 required query (`legal_entity_reference`, `book_reference`, `fiscal_period_reference`, `statement_type_code`) and the ADR 0025 optional comparison. Optional `statement_scope_code` is exactly `period` or `year_to_date`.

There is no fiscal-year column and no year table. Year identity is the leading `YYYY` on existing `fiscal_period.period_code` (`2026-08`), or `period_start_date` when that code has no year. AIS does not invent a year.

When the scope query is omitted, or is `period`, the response omits `statement_scope_code`. Income-statement lines are that period's operational revenue and expense, excluding AIS period-closing journals (ADR 0024). The balance sheet remains the as-of statement for `fiscal_period_reference`.

When the scope is `year_to_date`, the document adds `"statement_scope_code": "year_to_date"`. Income-statement lines sum operational revenue and expense across periods on the same calendar whose year identity matches and whose `period_start_date` is on or before the requested period, still excluding closing journals so monthly hard-close does not wipe year-to-date earnings. The year-to-date balance sheet is the same as-of sheet already returned for that period, including retained earnings parked on hard-close. Comparison uses the same `statement_scope_code`. Unknown scope is 400. Missing year identity is 400 and names the next action.

IAS 1 requires a statement of profit or loss for the period presented, and interim practice reports year-to-date totals for the current financial year alongside the statement of financial position as of the interim date (IFRS Foundation, 2022, 2023). This read places both scopes on the existing statement GET.

## Consequences

A controller can ask for this month or this year on one income-statement or balance-sheet GET. Omitting the scope query leaves the ADR 0021 document keys unchanged. Period P&L is no longer inception-to-date through `period_end_date`; that accumulation is the year-to-date scope when the activity sits in the same year.
