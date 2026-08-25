# ADR 0035: HTTP account-rollforward working paper

**Status:** Accepted

## Decision

AIS exposes `lookup_account_rollforward` and `GET /account-rollforwards?legal_entity_reference=&book_reference=&fiscal_period_reference=&chart_account_code=` on the same stdlib HTTP surface as account-balance inquiry (ADR 0034). The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. This slice is a one-account working paper, not a customer subledger and not a second statement type. AIS does not add a table or migration.

The document is one object, not a list. Amounts are exact unsigned decimal strings on each side, the same convention as `GET /account-balances`. `closing_debit_amount` equals `opening_debit_amount` plus `period_debit_amount`, and the credit sides add the same way. Those closing sides equal `GET /account-balances` for that account and period: hard-closed periods use the stored trial-balance snapshot; open and `soft_closed` periods stay live, including AIS adjusting journals.

Opening is the prior hard-close snapshot for that chart account when one exists, otherwise the live sum of posted lines before the scope start. Period activity is posted lines in the scope, including adjusting journals. After hard-close, the AIS period-closing journal is included on the accounts it touches so retained-earnings opening plus the close park equals snapshot closing. Empty activity on a known catalog account returns zeros rather than 404. Unknown legal entity, book, period, or chart account fails closed.

Optional `statement_scope_code` is exactly `period` or `year_to_date`, using the same year-identity rule as financial statements (ADR 0028). Omit or `period` keeps today's keys. `year_to_date` opening is the start of the first same-year period and the document then includes `statement_scope_code`. This slice does not add comparison. `POST /account-rollforwards` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

IAS 1's complete set of financial statements is accompanied by information that explains and supports those statements (IFRS Foundation, 2022). ADR 0034 placed the as-of balance next to the ledger; this read adds the opening-to-closing rollforward that ties that balance to period activity.

## Consequences

Controllers and auditors can prove opening + period debit/credit = closing for AR, cash, revenue, or retained earnings without SQL and without a second numerical truth. Account-balance, trial-balance, ledger, and financial-statement reads stay on their existing routes.
