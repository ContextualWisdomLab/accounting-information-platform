# ADR 0037: HTTP financial-statement close package

**Status:** Accepted

## Decision

AIS exposes `lookup_financial_statement_package` and `GET /financial-statement-packages?legal_entity_reference=&book_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as the single-statement read (ADR 0021). The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. This slice is a close pack, not a fifth statement type and not a second numerical truth. AIS does not add a table or migration.

Required query keys are the same as `GET /financial-statements` minus `statement_type_code`: `legal_entity_reference`, `book_reference`, and `fiscal_period_reference`. Optional `statement_scope_code` (`period` | `year_to_date`) and `comparison_fiscal_period_reference` keep the ADR 0025 / ADR 0028 meaning. The package includes `statement_scope_code` only for `year_to_date`. When comparison is supplied, every inner statement includes the existing `comparison_*` keys.

The document envelope repeats the tenant, legal entity, book, and period identity. Nested objects `income_statement`, `balance_sheet`, `changes_in_equity`, and `cash_flow` are the exact documents already returned by `lookup_financial_statement` / `GET /financial-statements` for those types and the same scope. The package calls that existing lookup; it does not reimplement statement math.

Empty books return four zero-amount statements rather than 404. Unknown legal entity, book, or period is 404. An unknown scope is 400. A tenant-header mismatch is rejected before the read and writes zero rows. `POST /financial-statement-packages` is 405. `GET /financial-statements` stays the single-statement route.

IAS 1's complete set of financial statements is a statement of financial position, a statement of profit or loss, a statement of changes in equity, and a statement of cash flows for the period (IFRS Foundation, 2022). This read places that complete set on one GET so a controller can take a period close pack without calling the statement route four times.

## Consequences

Controllers can retrieve the four statements that already tie to each other—period net income, closing equity to the balance sheet, and closing cash to cash-account 110200—without a second projection or a second route family. Single-statement, trial-balance, account-balance, and rollforward GETs stay unchanged.
