# ADR 0033: HTTP statement of cash flows

**Status:** Accepted

## Decision

AIS extends `lookup_financial_statement`, `load_financial_statement`, and `GET /financial-statements` with `statement_type_code=cash_flow`. The required query stays `legal_entity_reference`, `book_reference`, `fiscal_period_reference`, and `statement_type_code`. Optional `statement_scope_code` (`period` | `year_to_date`) and `comparison_fiscal_period_reference` keep the ADR 0025 / ADR 0028 meaning and omitted-key rules. AIS does not add a second statement route, a cash-flow class code, or a movement table.

The projection is the IAS 7 indirect method (IFRS Foundation, 2022). Cash accounts are chart accounts mapped from the existing `account_role_mapping` role `cash_receipt` (seeded 110200). Accounts receivable and revenue are not cash. `statement_lines` are eight movement rows identified by `account_role_code` with empty `chart_account_code` and empty `account_class_code`: `period_net_income`, `operating_working_capital`, `cash_from_operations`, `cash_from_investing`, `cash_from_financing`, `net_cash_change`, `opening_cash`, and `closing_cash`. Amounts follow the changes-in-equity credit-normal convention: a cash increase is `credit_amount` and a cash decrease is `debit_amount`.

`period_net_income` is the same operational result as `income_statement` for that scope (AIS period-closing journals excluded). `operating_working_capital` is the period change in asset and liability class accounts that are not cash accounts, signed `Δ(credit − debit)` so an AR increase reduces operating cash and a tax-payable increase is a source of cash. `cash_from_operations` is period net income plus that working-capital amount. `cash_from_investing` is 0 unless a non-cash non-equity asset acquisition can be proven from existing books; this slice does not invent investing activity. `cash_from_financing` uses the same equity-class journals as changes-in-equity `other_equity_movements` and excludes the AIS period-closing journal. `net_cash_change` is operations plus investing plus financing. `opening_cash` is the debit-normal cash-account balance at scope start: the latest prior hard-close snapshot when one exists, otherwise live cash-account journals before that date. `closing_cash` equals opening plus net cash change and equals the balance-sheet cash-account total for the same scope. After hard-close, period net income stays the pre-close operational result; the closing journal is not working capital or financing; closing cash still equals balance-sheet cash. Empty books return those eight rows at zero rather than 404.

## Consequences

A controller can produce the IAS 7 cash-flow statement that ties opening cash and period cash movements to the balance-sheet cash total without SQL and without a second numerical truth. Income-statement, balance-sheet, changes-in-equity, comparison, and year-to-date reads stay on the same route.
