# ADR 0043: Billing unapplied-cash refund maps to a liability clearing account

**Status:** Accepted

## Decision

AIS pins Billing #59, #60, and #61 leftover-cash journals (`metering-billing-platform` head `dde23ee`) on the existing ingest path. Each compose emits one `validated` `accounting_journal_proposal`. AIS does not invent a dedicated leftover-cash route or a statutory identifier from Billing.

The published lines are debit `unapplied_cash` and credit `cash_receipt` for the exact inclusive refund amount, same currency, balanced. `intended_book_role_code` is `primary_statutory`. The idempotency key is `{tenant}:unapplied_cash_refund:{unapplied_cash_refund_id}:{source_payload_hash}:v{version}`. Ordinary `ingest_journal_proposal`, `POST /journal-proposals`, and `billing_pull` accept that proposal the same way as `cash_receipt` / `tax_payable`. Pull stays GET `/v1/journal-proposals`. AIS does not flip Billing `proposal_status`.

The catalog seed adds chart account `210200` (`account_class_code=liability`, `normal_balance_code=credit`, `account_name=unapplied_cash`) and `account_role_code=unapplied_cash` → `210200`. Existing maps stay `cash_receipt` → 110200, `accounts_receivable` → 110100, `tax_payable` → 210100, `write_off_expense` → 510100, and `retained_earnings` → 310100. `GET /account-role-mappings` lists the new row. An unknown role remains 422. Billing `retained_earnings` remains 422.

The refund journal is ordinary Billing activity (`journal_source_code=billing`). It is not `period_closing`. Because 210200 is a liability, hard-close does not zero it into `retained_earnings` 310100. A refund remaining on 210200 is still there after hard-close. Soft-close still rejects a new ordinary post; replay of an already-posted refund key returns the original receipt.

Billing #60 leftover park uses debit `cash_receipt` / credit `unapplied_cash` for the exact inclusive leftover on `{tenant}:unapplied_cash:{unapplied_cash_id}:{source_payload_hash}:v{version}`. Billing #61 apply uses debit `unapplied_cash` / credit `accounts_receivable` on `{tenant}:unapplied_cash_application:{unapplied_cash_application_id}:{source_payload_hash}:v{version}`. Replay of each key returns the original receipt. AIS does not add a fourth role. After park then apply, `GET /receivable-agings` drops `total_outstanding_amount` by the applied amount using the same FIFO as a write-off credit: unsigned buckets, and excess apply credit uses `unapplied_credit_amount`.

`GET /payable-agings` stays the catalog `tax_payable` account (210100). Optional `chart_account_code=210200` is 422. This slice does not invent `party_reference`.

IAS 1 requires a statement of financial position that presents liabilities separately from profit or loss for the period (IFRS Foundation, 2022). Unapplied cash is leftover commercial clearing, not an income-statement account, so the closer must leave 210200 on the sheet.

## Consequences

Controllers can post a Billing leftover park, apply-to-AR, or refund through the existing proposal boundary, read `unapplied_cash` → 210200 from the catalog, see receivable aging drop by an apply, and hard-close without parking that liability into retained earnings. Payable aging stays tax payable. Cash, AR, tax, write-off, and credit-unwind ingest stay on their existing roles.
