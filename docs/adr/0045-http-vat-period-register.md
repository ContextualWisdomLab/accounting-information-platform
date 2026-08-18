# ADR 0045: HTTP period VAT register

**Status:** Accepted

## Decision

AIS exposes `lookup_vat_period_register` and `GET /vat-period-registers?legal_entity_reference=&book_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as payable aging (ADR 0041). The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. This slice is the statutory tax-invoice register / period VAT bundle that ties to posted catalog `tax_payable` 210100. Billing still owns commercial tax assessment. AIS does not add a catalog role, a table, or a migration, and does not build NTS/HomeTax transmission, an issuance command, or a Billing tax adapter.

The document is one object, not a page. Amounts are exact decimal strings. Always-present keys are `tenant_reference`, `legal_entity_reference`, `accounting_book_reference`, `book_reference`, `fiscal_period_reference`, `as_of_date` (that fiscal period's `period_end_date`), `chart_account_code` `210100`, `account_role_code` `tax_payable`, `issued_amount`, `voided_amount`, and `closing_amount`. AIS does not add `party_reference`, `next_cursor`, NTS submission fields, or invoice status.

`issued_amount` is the as-of sum of credits to 210100 on issued-invoice tax journals. Journals are identified by the published `:invoice_draft:` idempotency-key prefix or by the posted issued role pair (debit `accounts_receivable`, credit `usage_revenue`, credit `tax_payable`). `voided_amount` is the as-of sum of debits to 210100 on published issued-invoice-void journals. Journals are identified by the published `:issued_invoice_void:` prefix or by the void role pair (debit `usage_revenue`, debit `tax_payable`, credit `accounts_receivable`). A taxed credit adjustment that posts that same void role pair therefore reduces output VAT as `voided_amount`. AIS does not invent `tax_invoice`, `nts`, or `input_vat` roles and does not include leftover-cash 210200.

Reads are as-of `period_end_date` from live journals, the same window as payable aging, so the register can equal `GET /account-balances` 210100 (`credit_amount` − `debit_amount`) and `GET /payable-agings` 210100 `total_outstanding_amount` without inventing an `opening_amount` key. The AIS period-closing journal is excluded. Soft-closed periods stay live. Hard-closed periods still read live tax journals through period end.

`closing_amount` is `issued_amount − voided_amount`. Adjusting journals that touch 210100 stay in that tie-out. When a 210100 line cannot be classified as issued or voided, the document includes `other_movement_amount` so `closing_amount = issued_amount − voided_amount + other_movement_amount`; that key is omitted when the amount is zero. Empty tax history, including an untaxed invoice-only book, returns zeros rather than 404. Unknown legal entity, book, or period is 404. `POST /vat-period-registers` is 405. A tenant-header mismatch is rejected before the read and writes zero rows. Optional `book_reference` may be supplied as `accounting_book_reference`, the same alias as other book-scoped catalog GETs.

IAS 1 requires presentation that helps users assess financial position, including current liabilities, accompanied by information that explains those statements (IFRS Foundation, 2022). Output VAT payable is that current liability. Controllers need issued versus voided tax next to the 210100 balance so they can prove the tax-invoice register ties to posted `tax_payable`.

## Consequences

Controllers can reconcile period VAT on 210100 without SQL, without a new catalog role, and without an NTS adapter. Account-balance, payable-aging, leftover-cash, and period-close-package reads stay on their existing routes. Billing still assesses commercial tax and still posts issued invoices and issued-invoice voids on the existing ingest path. AIS now also exposes a fail-closed HomeTax command (ADR 0046) that requires this register before any filing attempt and still does not transmit to NTS.
