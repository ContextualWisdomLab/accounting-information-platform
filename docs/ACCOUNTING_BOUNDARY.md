# Accounting and Billing Boundary

## Metering Billing Platform owns

- usage attribution and metering;
- price books and customer contracts;
- rating, credits, quotas, and entitlement;
- invoice intent and commercial billing subledger;
- payment, refund, dispute, and provider settlement facts;
- provider and cash expectation reconciliation;
- the authoritative schema for `accounting_journal_proposal`.

## Accounting Information Platform owns

- legal entity and accounting book;
- chart of accounts and semantic account-role mapping;
- fiscal calendar, period state, and close control;
- accounting policy and posting rules;
- authoritative general journals and reversals;
- trial balance, close, consolidation, and reporting projections;
- the authoritative schema for `accounting_posting_receipt`.

## Required interaction

```text
billing invoice or settlement fact
-> balanced semantic journal proposal
-> accounting policy resolution
-> posted / held / rejected / reversed receipt
-> source-to-posting reconciliation reference
```

A proposal may name `accounts_receivable`, `usage_revenue`, `cash_receipt`, `contract_liability`, `tax_payable`, `cash_clearing`, or `provider_fee_expense`. Accounting maps those roles to chart accounts under an effective policy version. Source systems cannot bypass this mapping by sending chart-account IDs.

## Revenue caution

Invoice issuance, payment capture, and provider payout do not by themselves determine revenue recognition. Performance obligation, principal-versus-agent, variable consideration, contract liability, and period-of-recognition policy belong to the accounting layer and require approved policy review before production use.
