# Data Model

The foundation ERD is maintained in [ERD.md](ERD.md). PostgreSQL migrations are the executable source of truth for columns, constraints, triggers, row-level security, and indexes.

## Authoritative master data

- `tenant_account`: local tenant authority boundary.
- `legal_entity_record`: effective-dated legal entity and functional currency.
- `accounting_book`: statutory, management, tax, or consolidation book assignment.
- `chart_account`: effective-dated chart account within a book, including durable `account_class_code` (`asset`, `liability`, `equity`, `revenue`, or `expense`).
- `account_role_mapping`: semantic source role to approved chart account under policy and rule versions.
- `fiscal_calendar` and `fiscal_period`: period identity and close state.

## Journal and evidence data

- `journal_proposal_record`: immutable external proposal identity, contract version, tenant-scoped idempotency key, and source-payload hash.
- `fiscal_period_open_command`: append-only period-open command evidence binding tenant, legal entity, fiscal period, tenant-scoped idempotency key, canonical source-payload hash, and requested dates. Exact retries replay this evidence; changed payload under the same key conflicts.
- `general_journal`: authoritative posted or reversed header.
- `journal_entry_line`: one-sided exact debit or credit mapped to a chart account.
- `journal_source_reference`: evidence references and hashes attached to a journal.
- `journal_reversal`: original-to-reversal lineage; a reversal is a new append-only journal rather than a mutation of the original.
- `posting_receipt`: source-facing authoritative outcome for the proposal command.
- `outbox_event`: transactionally committed publication record associated with authoritative accounting state.

## Reporting and tax evidence data

- `trial_balance_snapshot`: immutable population and currency snapshot for one book and period, including the hard-close `close_idempotency_key`.
- `trial_balance_line`: exact debit, credit, and net values per chart account.
- `home_tax_submission`: fail-closed HomeTax filing-command receipt for one entity, book, and period. The row preserves the tenant-scoped `submission_idempotency_key`, canonical command `source_payload_hash`, immutable `source_payload_reference`, `submission_status_code`, `rejection_reason_code`, `as_of_date`, `closing_amount`, and derived `register_payload_hash`. It does not store raw register JSON, NTS payloads, or credentials.

Financial-statement, cash-flow, changes-in-equity, aging, account-balance, ledger, rollforward, VAT-register, and period-close-package reads are deterministic projections over authoritative journal, period, catalog, and snapshot facts. They do not create a second statutory ledger.

## Normalization and integrity rules

- Account role, chart account, journal line, period, command evidence, receipt, and publication event are separate facts.
- A provider, bank, or source-system identifier is never an internal primary key.
- Legal entity, book, chart account, fiscal period, journal, receipt, and tax-command references preserve tenant scope through composite keys where the relationship crosses tables.
- Historical master-data rows close their validity interval rather than being overwritten.
- Posted journals are never updated or deleted; finalized journal populations cannot be extended after receipt issuance.
- Exact debit and credit amounts use PostgreSQL `numeric` and application `Decimal`; binary floating-point accounting amounts are rejected at input boundaries.
- Command idempotency is tenant-scoped and tied to immutable source/command evidence so exact retries replay and changed evidence fails closed.

## Future extensions

Revenue contracts and performance obligations, durable receivable/payable subledgers, cash-application evidence, bank-statement reconciliation, foreign-exchange rates and remeasurement, fixed assets, intercompany balances and eliminations, consolidation, and reporting-taxonomy mappings are later normalized modules. They will reference, not duplicate, the journal authority and will not let external statement or model output post accounting facts automatically.

## Runtime tenant binding

`accounting_core.runtime_tenant_binding` is a normalized control-plane relation from PostgreSQL runtime role OID/name to `tenant_account`. `valid_from`, `valid_to`, and `recorded_at` preserve assignment history; one partial unique index permits only one active binding per role OID. Runtime roles cannot directly read or mutate this relation. Its active row is resolved through the no-argument `current_tenant_account_id()` security-definer function.
