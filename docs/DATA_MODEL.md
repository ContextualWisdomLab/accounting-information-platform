# Data Model

## Authoritative master data

- `tenant_account`: local tenant authority boundary.
- `legal_entity_record`: effective-dated legal entity and functional currency.
- `accounting_book`: statutory, management, tax, or consolidation book assignment.
- `chart_account`: effective-dated chart account within a book, including durable `account_class_code` (`asset`, `liability`, `equity`, `revenue`, or `expense`).
- `account_role_mapping`: semantic source role to approved chart account under policy and rule versions.
- `fiscal_calendar` and `fiscal_period`: period identity and close state.

## Journal and evidence data

- `journal_proposal_record`: immutable external proposal identity, contract version, idempotency key, and payload hash.
- `general_journal`: authoritative posted or reversed header.
- `journal_entry_line`: one-sided exact debit or credit mapped to a chart account.
- `journal_source_reference`: evidence references and hashes.
- `journal_reversal`: original-to-reversal lineage.
- `posting_receipt`: source-facing authoritative outcome.
- `outbox_event`: transactionally committed publication record.

## Reporting data

- `trial_balance_snapshot`: immutable population and currency snapshot for one book and period.
- `trial_balance_line`: exact debit, credit, and net values per chart account.

## Normalization rules

- Account role, chart account, journal line, period, and receipt are separate facts.
- A provider, bank, or source-system identifier is never an internal primary key.
- Legal entity, book, chart account, and fiscal period references include tenant-scoped composite foreign keys.
- Historical master-data rows close their validity interval rather than being overwritten.
- Posted journals are never updated or deleted.

## Future extensions

Revenue contracts, performance obligations, revenue schedules, receivables, cash receipts, bank transactions, foreign-exchange rates, intercompany balances, eliminations, financial statements, and reporting taxonomy mappings are later normalized modules. They will reference, not duplicate, the journal authority.
