# ADR 0043: Billing unapplied-cash refund maps to a liability clearing account

**Status:** Accepted

## Decision

AIS pins Billing #59 (`metering-billing-platform` head `dde23ee`) on the existing ingest path. `POST /v1/unapplied-cash-refunds/{unapplied_cash_refund_id}/journal-proposals` emits one `validated` `accounting_journal_proposal`. AIS does not invent a second refund contract, a refund route, or a statutory identifier from Billing.

The published lines are debit `unapplied_cash` and credit `cash_receipt` for the exact inclusive refund amount, same currency, balanced. `intended_book_role_code` is `primary_statutory`. The idempotency key is `{tenant}:unapplied_cash_refund:{unapplied_cash_refund_id}:{source_payload_hash}:v{version}`. Ordinary `ingest_journal_proposal`, `POST /journal-proposals`, and `billing_pull` accept that proposal the same way as `cash_receipt` / `tax_payable`. Pull stays GET `/v1/journal-proposals`. AIS does not flip Billing `proposal_status`.

The catalog seed adds chart account `210200` (`account_class_code=liability`, `normal_balance_code=credit`, `account_name=unapplied_cash`) and `account_role_code=unapplied_cash` → `210200`. Existing maps stay `cash_receipt` → 110200, `accounts_receivable` → 110100, `tax_payable` → 210100, `write_off_expense` → 510100, and `retained_earnings` → 310100. `GET /account-role-mappings` lists the new row. An unknown role remains 422. Billing `retained_earnings` remains 422.

The refund journal is ordinary Billing activity (`journal_source_code=billing`). It is not `period_closing`. Because 210200 is a liability, hard-close does not zero it into `retained_earnings` 310100. A refund remaining on 210200 is still there after hard-close. Soft-close still rejects a new ordinary post; replay of an already-posted refund key returns the original receipt.

A later park compose (debit `cash_receipt` / credit `unapplied_cash`) uses the same two catalog roles. AIS does not add a second role for that park.

`GET /payable-agings` stays the catalog `tax_payable` account (210100). Optional `chart_account_code=210200` is 422. This slice does not invent `party_reference`.

IAS 1 requires a statement of financial position that presents liabilities separately from profit or loss for the period (IFRS Foundation, 2022). Unapplied cash is leftover commercial clearing, not an income-statement account, so the closer must leave 210200 on the sheet.

## Consequences

Controllers can post a Billing unapplied-cash refund through the existing proposal boundary, read `unapplied_cash` → 210200 from the catalog, and hard-close without parking that liability into retained earnings. Payable aging stays tax payable. Cash, AR, tax, write-off, and credit-unwind ingest stay on their existing roles.
