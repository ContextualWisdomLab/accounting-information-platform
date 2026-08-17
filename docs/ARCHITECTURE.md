# Architecture

## Authority topology

```text
Operational systems         Metering Billing Platform
        |                              |
        +-------- economic facts ------+
                                       |
                            accounting_journal_proposal
                                       |
                                       v
                    Accounting Information Platform
                    - proposal intake and evidence
                    - policy and mapping resolution
                    - period and currency controls
                    - immutable journals and reversals
                    - trial balance and close
                    - financial reporting projections
                                       |
                          accounting_posting_receipt
                                       |
                  source reconciliation and audit evidence
```

## Bounded modules

| Module | Responsibility |
|---|---|
| `proposal_intake` | schema, source authority, idempotency, payload identity |
| `policy_resolution` | entity, book, period, currencies, account-role mapping |
| `journal_posting` | exact balance, immutable journal and lines, source lineage |
| `journal_reversal` | equal-and-opposite correction and replacement lineage |
| `trial_balance` | deterministic journal population aggregation |
| `close_control` | period states, hold queues, close and reopen governance |
| `reporting_projection` | versioned trial-balance and financial-statement views |
| `integration_outbox` | authoritative receipt and event publication after commit |

## Current implementation

`accounting_information_platform.core` is the reference implementation for proposal validation, policy checks, posting, reversal, and trial balance. It has no network or database dependency. `ingest_journal_proposal` reads the Billing-owned JSON contract field `proposal_status` and accepts only `validated` or `exported` before constructing a status-free `JournalProposal`. `accept_journal_proposal` and `POST /journal-proposals` ingest a Billing proposal and return `accounting_posting_receipt`. `lookup_published_receipt` and `GET /posting-receipts?idempotency_key=` return that same persisted receipt for a later Billing read. `pull_validated_journal_proposals` and `accept_pulled_proposals` GET Billing #15 `validated` pages (body keys `journal_proposals` + `next_cursor` only; query `cursor`; default `page_limit` 50, maximum 100) and post through `accept_journal_proposal` without flipping Billing `proposal_status`; `POST /billing-proposal-pulls` exposes that command. `accept_period_close` and `POST /period-closes` take optional body `period_status_code` (`soft_closed` or `hard_closed`; omit is `hard_closed`). Soft-close rejects ordinary posts, allows append-only reversal, and writes no snapshot; hard-close snapshots the book and rejects posts and reversals. `lookup_trial_balance` and `GET /trial-balances` return snapshot totals for a hard-closed period or live aggregation for an open or soft-closed period. `lookup_posted_journal` and `GET /journals?idempotency_key=` or `GET /journals?journal_reference=` return one persisted journal and its lines; `lookup_period_journals` and `GET /journals?legal_entity_reference=&fiscal_period_reference=&book_reference=` list existing posted and reversing journals for that period (`page_limit` default 50, maximum 100, `next_cursor` on `accounting_date|journal_reference`). `lookup_outbox_events` and `GET /outbox-events?event_type_code=` return unpublished `outbox_event` rows (`payload_reference` is the existing receipt, snapshot, or soft-close fiscal-period URN); `publish_outbox_event` and `POST /outbox-events/{outbox_event_id}/publish` set `published_at` on that tenant row. `lookup_fiscal_periods` and `GET /fiscal-periods?legal_entity_reference=` list existing `fiscal_period` rows for that tenant entity (`page_limit` default 50, maximum 100, `next_cursor` on `period_start_date|period_code`); single GET by `fiscal_period_reference` remains the period-status read. `lookup_account_ledger` and `GET /account-ledgers?legal_entity_reference=&chart_account_code=` return posted journal lines for one statutory chart account, optionally scoped by `fiscal_period_reference`, with exact `period_debit_total` / `period_credit_total` for the filtered scope. `lookup_accounting_books` and `GET /accounting-books?legal_entity_reference=` list existing `accounting_book` rows for that tenant entity (`accounting_book_reference` / `book_reference` from `book_name`, `intended_book_role_code` from `book_role_code`, and `book_name`); empty entities return `accounting_books` []. `lookup_chart_accounts` and `GET /chart-accounts?legal_entity_reference=&book_reference=` return existing `chart_account` rows (`chart_account_code`, `account_name`, `normal_balance_code`, `account_class_code`). `lookup_financial_statement` and `GET /financial-statements?legal_entity_reference=&book_reference=&fiscal_period_reference=&statement_type_code=` project an income statement or balance sheet from the same trial-balance aggregation as `GET /trial-balances`, using `account_class_code` to split revenue/expense from asset/liability/equity. `GET /healthz` is an ops probe. The HTTP server binds `0.0.0.0:$PORT` and accepts only the purpose-limited `X-CWL-Tenant-Reference` header on accounting routes. `PostgresPostingLedger.post_proposal` resolves `AccountingPolicy` from the foundation catalog (`account_role_mapping`, book by intended role, open fiscal period) in the same transaction as the post. `PostgresPostingLedger` also applies those invariants to PostgreSQL 18 through `database/migrations/0001_accounting_foundation.sql` and `database/migrations/0002_chart_account_class.sql`: one transaction writes the proposal, journal, lines, receipt, and outbox event; replay returns the original receipt; reversal is append-only; `close_fiscal_period` sets `soft_closed` without a snapshot or writes a trial-balance snapshot with `hard_closed` in one commit; a non-open fiscal period writes zero ordinary-posting rows. The in-memory `PostingLedger.post(proposal, policy)` path remains the reference oracle.

## Deployment evolution

1. Reference core and contracts.
2. PostgreSQL proposal-intake and posting transaction.
3. Journal-proposal accept HTTP POST and operator hold queue.
4. Billing integration and source-to-posting reconciliation.
5. Revenue accounting and settlement accounting.
6. Cash, ISO 20022 adapters, multi-currency, reporting, and consolidation.

The modules may remain in one deployable service until throughput, data residency, or independent control ownership justifies separation. Service boundaries must not introduce direct cross-service SQL.
