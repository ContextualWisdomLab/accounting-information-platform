# ADR 0044: HTTP leftover-cash rollforward working paper

**Status:** Accepted

## Decision

AIS exposes `lookup_unapplied_cash_rollforward` and `GET /unapplied-cash-rollforwards?legal_entity_reference=&book_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as account-balance inquiry (ADR 0034) and the one-account rollforward (ADR 0035). The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. This slice is one leftover-cash working paper for catalog liability 210200, not a party subledger and not a second statement type. AIS does not add a table or migration.

The document is one object, not a page. Amounts are exact decimal strings. `parked_amount` is the in-scope sum of Billing #60 park credits on `unapplied_cash` (debit `cash_receipt` / credit `unapplied_cash`). `applied_amount` is the in-scope sum of Billing #61 apply debits (debit `unapplied_cash` / credit `accounts_receivable`). `refunded_amount` is the in-scope sum of Billing #59 refund debits (debit `unapplied_cash` / credit `cash_receipt`). Journals are identified by the published idempotency-key prefixes (`:unapplied_cash:`, `:unapplied_cash_application:`, `:unapplied_cash_refund:`) or by those posted role pairs. AIS does not invent `party_reference`.

`opening_amount` is the credit-normal 210200 opening: the prior hard-close snapshot net when one exists, otherwise live `credit_amount − debit_amount` before the period start. `closing_amount` is `opening_amount + parked_amount − applied_amount − refunded_amount` and equals `GET /account-balances` 210200 (`credit_amount − debit_amount`) for the same entity, book, and period. Soft-closed periods stay live. Hard-closed periods still read live leftover journals through `period_end_date`, the same as aging (ADR 0039 / ADR 0041). Catalog 210200 does not clear into retained earnings.

Adjusting journals that touch 210200 stay in the opening and closing tie-out. When a 210200 line cannot be classified as park, apply, or refund, the document includes `other_movement_amount` so the equality still holds; that key is omitted when the amount is zero. Empty leftover history returns zeros rather than 404. Unknown legal entity, book, or period is 404. `POST /unapplied-cash-rollforwards` is 405. A tenant-header mismatch is rejected before the read and writes zero rows. Optional `book_reference` may be supplied as `accounting_book_reference`, the same alias as other book-scoped catalog GETs.

IAS 1 requires a statement of financial position that presents liabilities separately from profit or loss for the period, accompanied by information that explains and supports those statements (IFRS Foundation, 2022). Unapplied cash is leftover commercial clearing. Controllers need the park / apply / refund loop next to the 210200 balance so they can prove the three journals tie to that liability.

## Consequences

Controllers can reconcile leftover cash on 210200 without SQL and without a fourth leftover role. Account-balance, account-rollforward, receivable-aging, and payable-aging reads stay on their existing routes. Billing leftover ingest stays on ADR 0043.
