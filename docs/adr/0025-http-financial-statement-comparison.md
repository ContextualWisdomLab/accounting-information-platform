# ADR 0025: HTTP financial-statement prior-period comparison

**Status:** Accepted

## Decision

`lookup_financial_statement`, `load_financial_statement`, and `GET /financial-statements` keep the ADR 0021 required query (`legal_entity_reference`, `book_reference`, `fiscal_period_reference`, `statement_type_code`). Optional `comparison_fiscal_period_reference` projects that prior period with the same `statement_type_code` and the same live-or-snapshot rules already shipped (ADR 0023, ADR 0024).

When the comparison query is omitted, the response omits every `comparison_*` key so existing clients stay stable. When it is present, the document adds `comparison_fiscal_period_reference`, `comparison_statement_lines`, `comparison_total_debit_amount`, `comparison_total_credit_amount`, and `comparison_net_income_amount`. Line objects reuse the current `chart_account_code`, `account_role_code`, `account_class_code`, `debit_amount`, and `credit_amount` keys. An empty comparison period returns `comparison_statement_lines` [] and zero comparison totals. An unknown comparison period, or a comparison asked against a missing entity or book, fails closed as 404. Cross-tenant GET is 403 and writes zero rows. No second HTTP route is added.

IAS 1 requires comparative information for amounts reported in the current-period financial statements unless a standard permits otherwise (IFRS Foundation, 2022). This read places that prior period next to the current statement without a second numerical truth.

## Consequences

A controller can put this period beside the prior period on one income statement or balance sheet GET. Omitting the comparison query leaves the ADR 0021 document unchanged.
