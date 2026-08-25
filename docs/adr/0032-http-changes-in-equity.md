# ADR 0032: HTTP statement of changes in equity

**Status:** Accepted

## Decision

AIS extends `lookup_financial_statement`, `load_financial_statement`, and `GET /financial-statements` with `statement_type_code=changes_in_equity`. The required query stays `legal_entity_reference`, `book_reference`, `fiscal_period_reference`, and `statement_type_code`. Optional `statement_scope_code` (`period` | `year_to_date`) and `comparison_fiscal_period_reference` keep the ADR 0025 / ADR 0028 meaning and omitted-key rules. AIS does not add a second statement route or a movement table.

`statement_lines` are equity-movement rows, not a trial-balance dump. The four rows are `opening_equity`, `period_net_income`, `other_equity_movements`, and `closing_equity`, identified by `account_role_code` with `account_class_code=equity`. `period_net_income` is the same operational result as `income_statement` for that scope (AIS period-closing journals excluded). `other_equity_movements` are equity-class journals in the scope excluding that closing journal, so the retained-earnings park is not counted twice. `opening_equity` is equity-class net as of the scope start: the latest prior hard-close snapshot when one exists, otherwise live equity-class journals before that date. Soft-close and open periods stay live. `closing_equity` equals opening + period net income + other movements and equals the balance-sheet equity total for the same scope (equity-class net plus unparked `net_income_amount` before hard-close; parked 310100 after). Empty books return those four rows at zero rather than 404.

IAS 1's complete set of financial statements includes a statement of changes in equity for the period (IFRS Foundation, 2022). This read places that statement on the existing GET beside the income statement and balance sheet.

## Consequences

A controller can produce the equity rollforward that ties opening equity and period profit or loss to closing equity without SQL and without a second numerical truth. Income-statement, balance-sheet, comparison, and year-to-date reads stay on the same route.
