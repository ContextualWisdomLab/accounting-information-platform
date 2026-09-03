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

Financial-statement, cash-flow, changes-in-equity, aging, account-balance, ledger, rollforward, VAT-register, and period-close-package reads are deterministic projections over authoritative journal, period, catalog, and snapshot facts. They do not create a second statutory ledger.

## Canonical financial-report proposal

The first financial-reporting slice is deliberately stateless and adds no migration. Its document is an internally consistent **proposal**, not an authoritative report. It contains:

```text
report_contract_version
truth_status_code = proposed
source_authority_code = caller_supplied_statement_package
publication_readiness_code = unverified
authoritative_report = false
report_artifact_reference = urn:cwl:accounting:financial_report_proposal:{sha256}
report_artifact_hash
source_package_hash
tenant_reference
legal_entity_reference
book_reference
fiscal_period_reference
comparison_fiscal_period_reference?
statement_scope_code?
report_context
source_snapshot_references
profit_and_loss_summary
fact_records
explanation_records
source_statement_package
```

`report_context` contains a caller-supplied filing-independent entity identifier scheme/value, reporting currency, current start/end dates, optional comparison start/end dates, and decimal precision. At this layer these values are validated for shape and internal consistency only. They are not derived from AIS-owned legal entity, book, currency, fiscal calendar, or close facts.

`source_snapshot_references` contains the snapshot references claimed by the supplied statement package. A SHA-shaped value, database-looking identifier, or matching reference across four supplied statements does not prove that the snapshot exists in PostgreSQL.

Each `fact_record` contains:

```text
fact_code
fact_amount
period_context_code
statement_type_code
period_type_code
source_evidence_paths
```

Each `explanation_record` contains:

```text
explanation_code
status_code
direction_code
parameter_map
source_evidence_paths
```

The proposal retains its full source statement package so an exporter can reproduce every derived field without reopening the ledger. Its digest proves content identity, not source authority. A later object-storage implementation must classify and protect both proposal and authoritative report artifacts as financial evidence while preserving their distinct truth status.

## XBRL proposal value model

The stateless `XbrlTaxonomyProfile` value object contains:

```text
profile_identifier
profile_version
reporting_standard_code
taxonomy_release_code
taxonomy_prefix
taxonomy_namespace_uri
schema_reference_uri
taxonomy_package_hash
concept_mappings
```

Each `XbrlConceptMapping` contains:

```text
fact_code
concept_local_name
period_type_code
```

The profile does not contain journal formulas and is not a taxonomy parser. It is a reviewed bridge from canonical proposal facts to one immutable official or custom taxonomy package. One profile cannot map two canonical facts to the same concept or map one fact more than once. A profile cannot attest that the supplied report data came from AIS.

The XBRL export result additionally contains:

```text
truth_status_code = proposed
source_authority_code = caller_supplied_statement_package
publication_readiness_code = unverified
authoritative_report = false
xbrl_validation_status_code = not_run
filing_readiness_code = not_ready
```

Those values are fixed by the low-level serializer and cannot be promoted by caller input.

## Planned normalized reporting registry

The current stateless proposal contract prepares the following 3NF owner path. Names are design candidates and require a migration ADR before they become executable truth.

### Report command, source authority, and artifact identity

- `financial_report_run`: one authenticated tenant/entity/book/period/report-purpose command, idempotency identity, owner-derived report context, knowledge cutoff, source package hash, close/live/provisional state, status, actor, decision, and recorded time.
- `financial_report_source`: links one run to the AIS-owned four-statement population, journal population or hard-close snapshot, fiscal calendar, reporting currency/policy, source artifact digest, and database transaction evidence.
- `financial_report_artifact`: immutable truth status, media type, object-storage reference, byte length, content digest, renderer/version, encryption, retention, legal-hold, supersession, and withdrawal evidence.
- `financial_report_fact`: normalized canonical fact code, period context, period type, statement type, exact amount, currency, and artifact/run identity.
- `financial_report_fact_evidence`: ordered AIS-owned statement path, source population reference, and snapshot/journal evidence for one fact.
- `financial_report_explanation`: explanation code, status, direction, locale-independent exact parameter bundle identity, evidence status, and review/publication status.
- `financial_report_explanation_evidence`: ordered fact/source references supporting one explanation.

The owner command accepts identifiers and purpose context, never report amounts. It derives reporting currency and date ranges from AIS-owned master/calendar facts and loads the statement package inside one PostgreSQL `REPEATABLE READ` transaction. Only this path may create an authoritative report identity after required validation and approval.

The database should not duplicate the complete report JSON into every normalized table. The immutable artifact preserves the canonical package; normalized rows support governed query, source-authority verification, validation, approval, and impact analysis.

### Taxonomy and mapping release

- `taxonomy_profile`: tenant/global scope, profile identity/version, reporting standard, taxonomy release, namespace, entry point, official package digest, license classification, release status, valid time, system time, and supersession.
- `taxonomy_concept_mapping`: profile-scoped canonical fact code, taxonomy concept identity, period type, balance type, sign/scale policy, dimensional applicability, reviewer, and evidence.
- `taxonomy_profile_release`: immutable profile manifest digest, source package provenance, approval, publication, and withdrawal receipt.

Official taxonomy text, labels, schemas, linkbases, or licensed files must not be copied into public fixtures without a compatible license. Store package references and digests or use a restricted artifact store.

### Validation, approval, and publication

- `report_validation_run`: artifact/profile/validator/version/command identity, start/end state, environment, and provenance.
- `report_validation_result`: rule/specification/jurisdiction code, severity, fact/context locator, message code, evidence, and resolution state.
- `report_approval_record`: maker-checker decision over one exact owner-bound artifact, source population, taxonomy profile, validation population, purpose, locale, and publication target.
- `report_publication_receipt`: immutable output identity, destination, delivery/submission reference, acceptance/rejection state, regulator/customer evidence, and recorded time.
- `report_withdrawal_record`: append-only withdrawal/supersession decision and successor artifact reference.

A validation success does not post journals, prove source authority, approve a report, or prove regulator acceptance. An approval does not alter source financial facts. A publication receipt does not overwrite the artifact or historical filing state.

## Bank-statement evidence data

- `bank_account_record`: tenant-scoped opaque bank-account identity with `account_currency_code` and `account_identifier_hash`. Generic list/read models do not require a plaintext bank-account identifier.
- `bank_account_assignment`: effective-dated binding of one bank account to a legal entity, accounting book, and same-book cash/bank chart account. PostgreSQL requires the book to belong to that same legal entity through the composite `(tenant_account_id, legal_entity_id, accounting_book_id)` foreign key. Migration `0012_bank_assignment_command_identity.sql` adds tenant-scoped `assignment_idempotency_key` replay identity with an immutable `assignment_command_hash`, so an exact retry returns the original binding while reuse of a key with different evidence fails closed; a partial unique index admits only one active (`valid_to IS NULL`) binding per tenant, bank account, and book.
- `bank_statement_artifact`: host evidence-store locator, `source_artifact_hash`, and byte length. The original XML is not stored as a durable database text column.
- `bank_statement_record`: one canonical statement population with `message_definition_identifier`, statement identity, sequence and period evidence, opening/closing balance hashes, `source_artifact_hash`, `normalized_payload_hash`, and `ingestion_idempotency_key`.
- `bank_statement_entry`: normalized entry facts including exact `numeric` amount, ISO currency, credit/debit indicator, source locator, bank-transaction codes, bounded remittance/counterparty projection, and `source_entry_hash`.
- `bank_statement_entry_detail`: one transaction-detail record when identity, matching, or amount conservation requires it. Those facts are not collapsed into an opaque JSON column.

## Normalization and integrity rules

- Account role, chart account, journal line, period, command evidence, receipt, report source, validation, approval, and publication event are separate facts.
- A provider, bank, regulator, filing, or source-system identifier is never an internal primary key.
- Legal entity, book, chart account, fiscal period, journal, receipt, tax-command, report, and profile references preserve tenant scope through composite keys where the relationship crosses tables.
- `general_journal` preserves legal-entity/book consistency with a composite foreign key, while `journal_entry_line` preserves same-book chart-account scope with a database trigger so the normalized line does not duplicate `accounting_book_id` merely to enforce the relationship.
- Historical master-data, taxonomy-profile, mapping, approval, and publication rows close their validity interval rather than being overwritten.
- Posted journals are never updated or deleted; finalized journal populations cannot be extended after receipt issuance.
- Report proposals, authoritative report artifacts, validation results, approvals, and publication receipts are append-only. Corrections create a new artifact and explicit supersession/withdrawal evidence.
- Exact debit, credit, fact, and validation amounts use PostgreSQL `numeric` and application `Decimal`; binary floating-point accounting amounts are rejected at input boundaries.
- Command idempotency is tenant-scoped and tied to immutable source/command evidence so exact retries replay and changed evidence fails closed.
- Content digest, caller claim, test fixture, or taxonomy profile cannot elevate truth status. Authority requires the owner command and retained PostgreSQL source provenance.
- XBRL profile mappings cannot change the source report amount. A sign, scale, dimension, unit, or concept transformation must be explicit, versioned, reviewed, and independently validated.
- Report facts, presentation labels, localized explanations, model-generated prose, validation findings, approvals, filing receipts, and regulator acceptance are separate facts.

## Future extensions

Revenue contracts and performance obligations, durable receivable/payable subledgers, cash-application evidence, deterministic bank-statement matching, foreign-exchange rates and remeasurement, fixed assets, intercompany balances and eliminations, consolidation, reporting-taxonomy profile persistence, statement notes, dimensions, segment reporting, EPS, and jurisdiction filing adapters are later normalized modules. They will reference, not duplicate, the journal authority and will not let an external statement, caller-supplied proposal, renderer, validator, regulator response, or model output post accounting facts or mint authoritative report origin automatically.

## Runtime tenant binding

`accounting_core.runtime_tenant_binding` is a normalized control-plane relation from PostgreSQL runtime role OID/name to `tenant_account`. `valid_from`, `valid_to`, and `recorded_at` preserve assignment history; one partial unique index permits only one active binding per role OID. Runtime roles cannot directly read or mutate this relation. Its active row is resolved through the no-argument `current_tenant_account_id()` security-definer function.

## Accounting-book period control

`accounting_book_period_control` is the authoritative close-state intersection of one tenant accounting book and one fiscal period. `fiscal_period` retains shared calendar dates; its status is an aggregate compatibility projection and must not be used to infer that every sibling book has the same close state. Trial-balance snapshots and journals already carry `accounting_book_id`, so close admission now uses the same scope.

## Soft-close command evidence fields

`accounting_book_period_control` carries nullable `soft_close_idempotency_key`, `soft_close_source_payload_hash` and `soft_close_source_journal_count` for migration compatibility. PostgreSQL requires them to be all absent or complete, makes non-null keys unique per tenant and prevents changes after a key is recorded. New application soft-closes always populate the complete set atomically; all-null values represent legacy rows whose original command evidence was not recoverable during migration.
