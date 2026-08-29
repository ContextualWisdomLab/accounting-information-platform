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
- `general_journal`: authoritative posted or reversed header. Its composite book reference requires the selected `accounting_book` to belong to the same tenant and `legal_entity_record`; independently valid entity/book identifiers cannot be mixed into one journal.
- `journal_entry_line`: one-sided exact debit or credit mapped to a chart account. The database `journal_line_book_scope_guard` rejects a chart account whose `accounting_book_id` differs from the parent journal rather than relying only on application lookup.
- `journal_source_reference`: evidence references and hashes attached to a journal.
- `journal_reversal`: original-to-reversal lineage; a reversal is a new append-only journal rather than a mutation of the original.
- `posting_receipt`: source-facing authoritative outcome for the proposal command.
- `outbox_event`: transactionally committed publication record associated with authoritative accounting state.

## Reporting and tax evidence data

- `trial_balance_snapshot`: immutable population and currency snapshot for one book and period, including the hard-close `close_idempotency_key`.
- `trial_balance_line`: exact debit, credit, and net values per chart account.
- `home_tax_submission`: fail-closed HomeTax filing-command receipt for one entity, book, and period. The row preserves the tenant-scoped `submission_idempotency_key`, canonical command `source_payload_hash`, immutable `source_payload_reference`, `submission_status_code`, `rejection_reason_code`, `as_of_date`, `closing_amount`, and derived `register_payload_hash`. It does not store raw register JSON, NTS payloads, or credentials.

## Bank-statement evidence data

- `bank_account_record`: tenant-scoped opaque bank-account identity with `account_currency_code` and `account_identifier_hash`. Generic list/read models do not require a plaintext bank-account identifier.
- `bank_account_assignment`: effective-dated binding of one bank account to a legal entity, accounting book, and same-book cash/bank chart account. PostgreSQL requires the book to belong to that same legal entity through the composite `(tenant_account_id, legal_entity_id, accounting_book_id)` foreign key. Migration `0012_bank_assignment_command_identity.sql` adds tenant-scoped `assignment_idempotency_key` replay identity with an immutable `assignment_command_hash`, so an exact retry returns the original binding while reuse of a key with different evidence fails closed; a partial unique index admits only one active (`valid_to IS NULL`) binding per tenant, bank account, and book.
- `bank_statement_artifact`: host evidence-store locator, `source_artifact_hash`, and byte length. The original XML is not stored as a durable database text column.
- `bank_statement_record`: one canonical statement population with `message_definition_identifier`, statement identity, sequence and period evidence, opening/closing balance hashes, `source_artifact_hash`, `normalized_payload_hash`, and `ingestion_idempotency_key`.
- `bank_statement_balance`: one immutable numeric balance fact per statement sequence, retaining optional balance type, exact amount, ISO currency, credit/debit direction, typed effective date/time, source locator, and source hash. Migration `0018_bank_statement_balance_evidence.sql` preserves the values that migration `0011_bank_statement_evidence.sql` previously represented only by opening/closing hashes; the effective date/time is separate from statement period and `recorded_at`, and missing numeric facts remain unavailable for a bridge rather than being inferred.
- `bank_statement_entry`: normalized entry facts including exact `numeric` amount, ISO currency, credit/debit indicator, source locator, bank-transaction codes, bounded remittance/counterparty projection, and `source_entry_hash`.
- `bank_statement_entry_detail`: one transaction-detail record when identity, matching, or amount conservation requires it. Those facts are not collapsed into an opaque JSON column.

## Reconciliation control evidence

Migrations `0013_reconciliation_run_exception_evidence.sql` through `0018_bank_statement_balance_evidence.sql` persist deterministic reconciliation as accounting control evidence without granting posting, reversal, close, or accounting-policy authority.

- `reconciliation_run`: immutable evaluated scope for one tenant, legal entity, accounting book, bank-account assignment, ISO currency, bank/book cutoffs, matching-policy version, and knowledge cutoff. Only the run status may progress; evaluated scope changes require a new run.
- `reconciliation_exception`: explicit operator-owned exception with an exception code, next action, effective/system time, and open/resolved/superseded resolution status.
- `reconciliation_evidence`: normalized evidence reference (and optional SHA-256 payload hash) attached to a run and, when applicable, to an exception.
- `reconciliation_candidate`: deterministic statement-entry-to-journal candidate with exact positive statement/journal source amounts and the rule that proposed it. Recorded candidates are append-only.
- `reconciliation_match`: reviewable candidate disposition (`proposed`, `approved`, `rejected`, or `superseded`). Multiple independent matches may be approved within a run when source-level conservation remains satisfied.
- `statement_match_allocation`: append-only exact amount consumed from an immutable statement source reference by one reconciliation match.
- `journal_match_allocation`: append-only exact amount consumed from an immutable journal source reference by one reconciliation match.
- `reconciliation_approval`: one immutable tenant/run/match-scoped human decision with command identity, immutable object-storage source-payload hash/reference, approver, purpose, decision, and effective/system times. PostgreSQL owns its version-1 snapshot hash over the candidate and allocation rows; a caller-supplied snapshot value is ignored.
- `ReconciliationClosePackage` is a read-only schema-versioned manifest over those rows, not another database authority. It carries every reviewed match identity with an approved decision, exact tenant/run scope, PostgreSQL snapshot digest, durable approval-evidence reference, immutable run cutoff, and source-population references; incomplete, rejected, or unrelated approval evidence fails closed before export.

Approved allocations are conserved by immutable source identity across active reconciliation runs in the same accounting/bank scope. Only matches whose current `match_status_code` is `approved` consume active capacity; `rejected` or `superseded` matches release capacity while their candidate and allocation rows remain durable historical evidence. Cross-run source-amount conflicts and over-consumption fail closed under database-owned guards and transaction-scoped advisory serialization. Approval and allocation transitions share a match-level advisory lock, and allocations plus candidate identity are frozen once approval evidence exists, so the durable decision cannot authorize a changed proposed state. Migration 0016 refuses to install over existing non-proposed matches that lack durable approval evidence; terminal approval timestamps remain immutable through explicit supersession. Reconciliation evidence therefore records and explains matching decisions but cannot itself post, reverse, close, or mutate authoritative journal facts.

Financial-statement, cash-flow, changes-in-equity, aging, account-balance, ledger, rollforward, VAT-register, and period-close-package reads are deterministic projections over authoritative journal, period, catalog, and snapshot facts. They do not create a second statutory ledger.

## Normalization and integrity rules

- Account role, chart account, journal line, period, command evidence, receipt, and publication event are separate facts.
- A provider, bank, or source-system identifier is never an internal primary key.
- Legal entity, book, chart account, fiscal period, journal, receipt, and tax-command references preserve tenant scope through composite keys where the relationship crosses tables.
- `general_journal` preserves legal-entity/book consistency with a composite foreign key, while `journal_entry_line` preserves same-book chart-account scope with a database trigger so the normalized line does not duplicate `accounting_book_id` merely to enforce the relationship.
- Historical master-data rows close their validity interval rather than being overwritten.
- Posted journals are never updated or deleted; finalized journal populations cannot be extended after receipt issuance.
- Exact debit and credit amounts use PostgreSQL `numeric` and application `Decimal`; binary floating-point accounting amounts are rejected at input boundaries.
- Command idempotency is tenant-scoped and tied to immutable source/command evidence so exact retries replay and changed evidence fails closed.

## Future extensions

Revenue contracts and performance obligations, durable receivable/payable subledgers, cash-application evidence, foreign-exchange rates and remeasurement, fixed assets, intercompany balances and eliminations, consolidation, and reporting-taxonomy mappings are later normalized modules. They will reference, not duplicate, the journal authority and will not let external statement or model output post accounting facts automatically.

## Runtime tenant binding

`accounting_core.runtime_tenant_binding` is a normalized control-plane relation from PostgreSQL runtime role OID/name to `tenant_account`. `valid_from`, `valid_to`, and `recorded_at` preserve assignment history; one partial unique index permits only one active binding per role OID. Runtime roles cannot directly read or mutate this relation. Its active row is resolved through the no-argument `current_tenant_account_id()` security-definer function.

## Accounting-book period control

`accounting_book_period_control` is the authoritative close-state intersection of one tenant accounting book and one fiscal period. `fiscal_period` retains shared calendar dates; its status is an aggregate compatibility projection and must not be used to infer that every sibling book has the same close state. Trial-balance snapshots and journals already carry `accounting_book_id`, so close admission now uses the same scope.

## Soft-close command evidence fields

`accounting_book_period_control` carries nullable `soft_close_idempotency_key`, `soft_close_source_payload_hash` and `soft_close_source_journal_count` for migration compatibility. PostgreSQL requires them to be all absent or complete, makes non-null keys unique per tenant and prevents changes after a key is recorded. New application soft-closes always populate the complete set atomically; all-null values represent legacy rows whose original command evidence was not recoverable during migration.
