# ADR 0034: HTTP as-of account-balance inquiry

**Status:** Accepted

## Decision

AIS exposes `lookup_account_balances` and `GET /account-balances?legal_entity_reference=&book_reference=&fiscal_period_reference=` on the same stdlib HTTP surface as account-ledger and trial-balance inquiry. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. This slice is a balance inquiry, not a fourth statement type and not a second trial-balance document. AIS does not add a table or migration.

Required query keys are `legal_entity_reference`, `book_reference`, and `fiscal_period_reference`. Balances are as of that period's close or end. After `hard_closed`, AIS reads the stored `trial_balance_snapshot` already written by period close (ADR 0006, ADR 0010, ADR 0024) and does not rebuild from journals when that snapshot exists. Open and `soft_closed` periods stay live: the sum of posted `journal_entry_line` amounts through the period end, including AIS adjusting journals. Closing journals exist only after hard-close, and the snapshot already includes them.

Each item is `chart_account_code`, `account_class_code`, and unsigned exact-decimal `debit_amount` / `credit_amount`, the same sides as ledger and trial-balance lines. The collection includes accounts with a non-zero posted total or that exist on the snapshot. Optional `chart_account_code` keeps that one account in `account_balances`; an unknown catalog code is 404. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `chart_account_code` order. Empty books return `account_balances` [] rather than 404. Missing legal entity, book, or period fails closed. `POST /account-balances` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

IAS 1's complete set of financial statements is accompanied by information that explains and supports those statements, including balances that let a reader tie the statements to the underlying accounts (IFRS Foundation, 2022). This read places that as-of inquiry next to the existing ledger and trial-balance GETs.

## Consequences

Controllers posting adjusting journals and auditors tying P&L, the balance sheet, and cash flow can read one rolled-up balance per chart account without SQL and without a second numerical truth. Hard-close remains snapshot-authoritative. Account-ledger lines, the period trial-balance document, and the financial-statement GET stay on their existing routes.
