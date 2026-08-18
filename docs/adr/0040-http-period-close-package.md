# ADR 0040: HTTP period-close binder

**Status:** Accepted

## Decision

AIS exposes `lookup_period_close_package` and `GET /period-close-packages?legal_entity_reference=&book_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as the financial-statement close pack (ADR 0037). The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. This slice is a controller binder, not a sixth statement, not a second trial balance, and not a second numerical truth. AIS does not add a table or migration.

Required query keys are the same as `GET /financial-statement-packages`: `legal_entity_reference`, `book_reference`, and `fiscal_period_reference`. Optional `statement_scope_code` (`period` | `year_to_date`) and `comparison_fiscal_period_reference` are forwarded only to the inner `financial_statement_package`. The binder envelope does not repeat those optional keys. Omit or `period` keeps the inner package's omit rule; `year_to_date` appears on that inner document only.

The document envelope repeats the tenant, legal entity, book, and period identity. Nested objects keep the same JSON as the standalone GETs: `fiscal_period` is the `GET /fiscal-periods` document for that period; `trial_balance` is `GET /trial-balances` with the basis omitted; `financial_statement_package` is `GET /financial-statement-packages`; `receivable_aging` is `GET /receivable-agings` for the catalog accounts-receivable account; `payable_aging` is `GET /payable-agings` for the catalog tax-payable account; and `unapplied_cash_rollforward` is `GET /unapplied-cash-rollforwards` for catalog 210200. Both aging documents and the leftover-cash rollforward use the same fiscal-period `period_end_date` as-of. Missing tax-payable activity still returns the standalone zero payable-aging document. Empty leftover history still returns the standalone zero leftover-cash document. `period_close` is the latest `GET /period-closes` item for that entity and period when a hard-close `trial_balance_snapshot` exists; otherwise it is `null`. Soft-close and open periods therefore keep `period_close` null. AIS does not invent a close receipt. The binder loads those worksheets inside one PostgreSQL `REPEATABLE READ` transaction on one shared session so a concurrent post cannot make aging disagree with trial balance, leftover cash, or statements inside one package document. Standalone GETs still open their own sessions.

Unknown legal entity, book, or period is 404. An unknown scope is 400. A tenant-header mismatch is rejected before the read and writes zero rows. `POST /period-close-packages` is 405. The single-route GETs stay unchanged.

IAS 1 requires a complete set of financial statements and information that accompanies that set so users can assess financial position and performance (IFRS Foundation, 2022). ADR 0037 placed the complete set on one GET. This read places the accompanying close evidence—period status, the omit-basis trial balance, that four-statement pack, entity-level receivable aging, entity-level payable aging, leftover-cash rollforward on catalog 210200, and the durable hard-close receipt when one exists—on one GET so a controller can bind a period close without seven separate calls.

## Consequences

Controllers can retrieve the documents they already use for close without a second projection or a second route family. Fiscal-period, trial-balance, financial-statement-package, receivable-aging, payable-aging, leftover-cash-rollforward, and period-close GETs stay unchanged.
