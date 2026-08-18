# ADR 0041: HTTP entity-level payable aging

**Status:** Accepted

## Decision

AIS exposes `lookup_payable_aging` and `GET /payable-agings?legal_entity_reference=&book_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as entity-level receivable aging (ADR 0039). The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. This slice is an entity-level liability control worksheet, not a vendor subledger and not a second numerical truth. AIS does not add a party or `party_reference`, a table, or a migration. Billing still owns counterparties. `GET /period-close-packages` includes this same payable-aging document as `payable_aging` next to `receivable_aging`.

Required query keys are `legal_entity_reference`, `book_reference`, and `fiscal_period_reference`. As-of is exactly that fiscal period's `period_end_date`. Optional `chart_account_code` defaults to the catalog `tax_payable` mapping (seeded 210100). An unknown catalog code is 404. A known AR, cash, revenue, or other non-catalog-payable code is 422; the only allowed account is that catalog payable account.

Aging reuses the receivable FIFO engine with the side inverted. Credit lines are payable increases (tax assessed) and age from stored `accounting_date`. Debit lines (credit tax-unwind, reversing payments) consume the oldest open credit first using exact decimal arithmetic. Adjusting journals that touch the payable account are included. The AIS period-closing journal is excluded and is not used as an assessment date. After hard-close, AIS still ages from live journals through period end; it does not invent dates from the trial-balance snapshot. `total_outstanding_amount` is the sum of the four unsigned buckets and equals `GET /account-balances` net (`credit_amount` − `debit_amount`) for that payable account and period, including the stored snapshot net after hard-close.

Buckets keep the ADR 0039 edges (`period_end_date` − remaining credit `accounting_date`): `current` is 0–30 inclusive, then `days_31_60`, `days_61_90`, and `days_over_90`. Empty payable history, including an untaxed invoice-only book, returns zero amounts rather than 404. Unknown legal entity, book, or period fails closed. `POST /payable-agings` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

IAS 1 requires presentation that helps users assess the entity's financial position, including current liabilities (IFRS Foundation, 2022). ADR 0039 placed entity-level receivable aging on that control path. This read is the matching liability aging worksheet for catalog tax payable. It does not split payables by vendor.

## Consequences

Controllers can take current versus past-due tax payable from the existing books and tie the total to the 210100 account-balance net without SQL and without inventing a party dimension. Vendor-level aging remains a Billing concern. The period-close binder now carries that same worksheet. Receivable-aging, payable-aging, trial-balance, and statement reads stay on their existing routes.
