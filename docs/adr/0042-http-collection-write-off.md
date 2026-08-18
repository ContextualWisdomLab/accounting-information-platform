# ADR 0042: Billing collection write-off maps to write-off expense

**Status:** Accepted

## Decision

AIS pins Billing #51 (`metering-billing-platform` head `ef68fc6`) on the existing ingest path. `POST /v1/collection-write-offs/{collection_write_off_id}/journal-proposals` emits one `validated` `accounting_journal_proposal`. AIS does not invent a second write-off contract, a write-off route, or a statutory identifier from Billing.

The published lines are debit `write_off_expense` and credit `accounts_receivable` for the exact inclusive write-off amount, same currency, balanced. `intended_book_role_code` is `primary_statutory`. The idempotency key is `{tenant}:collection_write_off:{collection_write_off_id}:{source_payload_hash}:v{version}`. Ordinary `ingest_journal_proposal`, `POST /journal-proposals`, and `billing_pull` accept that proposal the same way as `usage_revenue` / `tax_payable`. Pull is unchanged.

The catalog seed adds chart account `510100` (`account_class_code=expense`, `normal_balance_code=debit`) and `account_role_code=write_off_expense` → `510100`, the same mapping pattern as `usage_revenue` → `410100` and `tax_payable` → `210100`. AIS does not invent another expense code. `GET /account-role-mappings` lists that row. An unknown role remains 422.

The write-off journal is ordinary Billing activity (`journal_source_code=billing`). It is not `period_closing`. After hard-close, the existing closer zeros `510100` into `retained_earnings` 310100 like other P&L. `GET /financial-statements?statement_type_code=income_statement` includes the write-off expense and still excludes the AIS closing journal. Soft-close still rejects a new ordinary post; replay of an already-posted write-off key returns the original receipt.

IFRS 9 requires an entity to write off a financial asset, or a portion of it, when the entity has no reasonable expectations of recovering the contractual cash flows, and to recognize that write-off in profit or loss (IFRS Foundation, 2023). This catalog map is that statutory expense. It is not an expected-credit-loss allowance and does not invent a party dimension.

## Consequences

Controllers can post a Billing collection write-off through the existing proposal boundary, read `write_off_expense` → 510100 from the catalog, see the expense on the income statement, and hard-close it into retained earnings without treating the Billing journal as a closer. Cash, tax, credit-unwind, and payable-aging ingest stay on their existing roles.
