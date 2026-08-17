# Changelog

## [Unreleased]

### Fixed

- Pinned the Billing #15 list envelope to `journal_proposals` + `next_cursor` only (no body `items` or `cursor`); AIS sends `next_cursor` back as query `cursor`, defaults `page_limit` to 50, and rejects a limit above 100.
- Pinned the Billing-owned journal proposal contract to published `proposal_status` (`draft` | `validated` | `exported` | `rejected`) and ingest only `validated` or `exported` proposals; operational reject rows are not ingested.
- Hash-locked setuptools, wheel, and packaging so exact-head CI can install and wheel the package without build isolation or network resolution.
- Listed every CPython 3.13 coverage 7.15.4 wheel hash plus the universal wheel so ubuntu-latest can install the preferred manylinux x86_64 artifact under `--require-hashes`.

### Added

- `lookup_accounting_books` and `GET /accounting-books?legal_entity_reference=` list existing `accounting_book` rows for one tenant entity (`accounting_book_reference` / `book_reference` from `book_name`, `intended_book_role_code` from `book_role_code`, and `book_name`). Empty entities return `accounting_books` []. The catalog is small, so the list is not paged. `POST /accounting-books` is 405.
- `lookup_financial_statement` and `GET /financial-statements?legal_entity_reference=&book_reference=&fiscal_period_reference=&statement_type_code=` project an `income_statement` or `balance_sheet` from the same trial-balance totals as `GET /trial-balances`. Lines reuse TB `chart_account_code`, `debit_amount`, and `credit_amount`, plus catalog `account_role_code` and `account_class_code`. `net_income_amount` is credit-normal earnings on both statements. Empty books return `statement_lines` [] and zero totals. `POST /financial-statements` is 405.
- `database/migrations/0002_chart_account_class.sql` adds durable `account_class_code` (`asset` | `liability` | `equity` | `revenue` | `expense`) on `chart_account`. Seeded 110100 and 110200 are `asset`, 210100 is `liability`, and 410100 is `revenue`.
- `lookup_chart_accounts` and `GET /chart-accounts?legal_entity_reference=&book_reference=` return existing `chart_account` rows (`chart_account_code`, `account_name`, `normal_balance_code`, `account_class_code`) for one tenant entity and book. Empty books return `chart_accounts` []. `POST /chart-accounts` is 405.
- `lookup_account_ledger` and `GET /account-ledgers?legal_entity_reference=&chart_account_code=` return posted `journal_entry_line` rows for one statutory chart account (`line_number`, `chart_account_code`, `account_role_code`, `debit_amount`, `credit_amount`, plus `journal_reference` and `posted_at`). Optional `fiscal_period_reference` scopes the lines. `period_debit_total` and `period_credit_total` are exact decimals for the full filtered scope. Default `page_limit` is 50, maximum 100, and `next_cursor` is `posted_at|journal_reference|line_number`. Empty activity returns `ledger_lines` [].
- `lookup_fiscal_periods` and `GET /fiscal-periods?legal_entity_reference=` list existing `fiscal_period` rows for one tenant entity (`fiscal_period_reference`, `period_code`, `period_start_date`, `period_end_date`, `period_status_code`); default `page_limit` is 50, maximum 100, and `next_cursor` is `period_start_date|period_code`. Empty calendars return `fiscal_periods` []. Single GET by `fiscal_period_reference` and `POST /fiscal-periods` are unchanged.
- `lookup_outbox_events` and `GET /outbox-events?event_type_code=` list unpublished `accounting_integration.outbox_event` rows for one tenant (`outbox_event_id`, `event_type_code`, `aggregate_reference`, `payload_reference`, `payload_hash`, `created_at`); default `page_limit` is 50, maximum 100, and `next_cursor` is `created_at|outbox_event_id`. Empty unpublished sets return `outbox_events` []. `publish_outbox_event` and `POST /outbox-events/{outbox_event_id}/publish` set `published_at` on that tenant row; replay is idempotent. Persist writes `posting_receipt`, `journal_reversal`, and `period_close` in the same transaction as post, reverse, and close. `GET /posting-receipts` is unchanged.
- `lookup_period_journals` and `GET /journals?legal_entity_reference=&fiscal_period_reference=&book_reference=` list existing posted and reversing `general_journal` rows for one tenant period (`journal_reference`, `idempotency_key`, `journal_status_code`, `accounting_date`, `line_count`); default `page_limit` is 50, maximum 100, and `next_cursor` is `accounting_date|journal_reference`. Empty periods return `journals` []. Single-journal GET by key or reference is unchanged.
- Billing #20 taxed `credit_adjustment` proposals post the exact three-line Billing split (debit `usage_revenue` 410100, debit `tax_payable` 210100, credit `accounts_receivable` 110100) on the existing `{tenant}:credit_adjustment:{credit_adjustment_id}:{source_payload_hash}:v{version}` key; AIS does not recompute `credit_tax_amount` and does not add `tax_receivable`.
- Catalog `account_role_mapping` for `tax_payable` → 210100 (credit-normal current liability) so a Billing #19 three-line taxed invoice posts AR inclusive / revenue exclusive / tax payable through `post_proposal`; the invoice `invoice_draft` idempotency key is unchanged.
- `accept_period_open`, `lookup_fiscal_period`, `POST /fiscal-periods`, and `GET /fiscal-periods` open the next `fiscal_period` row (or replay an already-open period) and read existing period status and dates; hard-closed and soft-closed periods cannot be reopened.
- `lookup_posted_journal` and `GET /journals` return the persisted posted journal and its lines (`chart_account_code`, exact decimal debit/credit, `line_number`) by Billing `idempotency_key` or `journal_reference`; a reversing journal is returned when that identity is a reversal, missing journals fail closed, and `POST /journals` is 405.
- `lookup_account_role_mappings` and `GET /account-role-mappings` return the existing catalog `account_role_code` → `chart_account_code` rows plus stored policy and posting-rule versions for one tenant, legal entity, and book; missing catalog fails closed and `POST /account-role-mappings` is 405.
- Billing #17 `credit_adjustment` proposals reuse the published `accounting_journal_proposal` path (debit `usage_revenue` / credit `accounts_receivable`) with idempotency key `{tenant}:credit_adjustment:{credit_adjustment_id}:{source_payload_hash}:v{contract_version}`; no new account role or AIS mapping.
- `accept_journal_reversal` and `POST /journal-reversals` append an equal-and-opposite reversing journal through existing `PostgresPostingLedger.reverse` and return the reversing `accounting_posting_receipt`; the original receipt lookup is unchanged.
- `pull_validated_journal_proposals`, `pull_journal_proposal`, `accept_pulled_proposals`, `accept_billing_proposal_pull`, and `POST /billing-proposal-pulls` pull Billing #15 `validated` journal-proposal pages (`journal_proposals` + `next_cursor`) and post them without flipping Billing `proposal_status`.
- `accept_period_close`, `lookup_trial_balance`, `POST /period-closes`, and `GET /trial-balances` close a fiscal period and read snapshot or live trial-balance totals over the same purpose-limited HTTP surface; `GET /healthz` is an ops probe.
- `lookup_published_receipt` and `GET /posting-receipts` return the persisted `accounting_posting_receipt` for a purpose-limited tenant and Billing idempotency key without inventing a missing receipt.
- Catalog `account_role_mapping` for `cash_receipt` so a Billing `validated` cash proposal posts debit cash / credit accounts receivable through `post_proposal`.
- `accept_journal_proposal` and a stdlib `POST /journal-proposals` boundary that return the AIS posting-receipt contract, scoped by purpose-limited `X-CWL-Tenant-Reference`.
- Catalog policy resolution on `PostgresPostingLedger` so a Billing `JournalProposal` posts through `post_proposal` using AIS `account_role_mapping`, book, period, and policy versions rather than a caller-invented chart mapping.
- First-class fiscal-period close on `PostgresPostingLedger` that snapshots the trial-balance population and sets `soft_closed` or `hard_closed` in one transaction, with idempotent re-close and zero-row rejection of later ordinary posts.
- PostgreSQL 18 posting adapter that persists a balanced journal, idempotent replay, append-only reversal, and trial-balance tie-out through the foundation migration in one commit boundary.
- Hash-locked psycopg 3.3.4 and CPython 3.13 psycopg-binary wheels for exact-head persistence tests.
- Initial Accounting Information Platform product and authority baseline.
- Executable exact-decimal journal proposal, posting, reversal, and trial-balance reference core.
- Consumer contract for Metering Billing Platform journal proposals.
- Authoritative posting-receipt and accounting-policy manifest contracts.
- PostgreSQL 18.4 normalized accounting foundation with tenant-scoped foreign keys and row-level security.
- PRD, TRD, architecture, data model, security, testing, operability, ADRs, and APA 7th standards traceability.
- Offline repository-contract validation and commit-pinned exact-head CI.

### Security

- Rejected card data, PAT plaintext, provider secrets, prompt text, and response text from accounting contracts.
- Required immutable source-payload hashes and fail-closed idempotency conflicts.
