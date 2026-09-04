# Product Requirements Document

## Product outcome

Finance teams can receive accounting proposals from CWL products, resolve each proposal through approved accounting policy, post or hold it without duplication, reverse it without destroying history, prove every trial-balance amount back to source evidence, and prepare exact-value financial-report proposals that a future PostgreSQL-owned report command can validate, approve, and publish without recalculating ledger truth.

## Primary users

- Controllers own accounting policy, chart-account mappings, books, close controls, reporting profiles, and report approval.
- Accounting operations review held and rejected proposals and perform approved reversals.
- Finance platform engineers operate proposal intake, posting, outbox, reconciliation, report generation, validation, publication, and evidence retention.
- Financial-report preparers review profit-or-loss movement, cross-statement controls, taxonomy mappings, explanations, and export validation evidence.
- Auditors trace a financial statement, report fact, XBRL fact, or trial-balance balance back through statement lines, journal lines, posting receipts, source proposals, source payload hashes, report runs, taxonomy profiles, validation results, approvals, and publication receipts.

## Core jobs

1. Accept a versioned `accounting_journal_proposal` from an approved source.
2. Detect exact replay versus conflicting reuse of an idempotency key.
3. Resolve tenant, legal entity, accounting book, fiscal period, currencies, account roles, and policy versions.
4. Post a balanced immutable journal or return a structured hold or rejection.
5. Reverse an original journal with an equal-and-opposite journal while preserving both.
6. Produce a trial balance that ties exactly to the included journal population.
7. Return an authoritative `accounting_posting_receipt` to the source system.
8. Canonicalize a caller-supplied four-statement-shaped package into a deterministic **unverified report proposal** without recalculating its values.
9. Explain profit-or-loss movement and statement controls through exact parameters, status codes, and source evidence paths rather than unsupported prose.
10. Export mapped proposal facts as a deterministic, explicitly unverified XBRL 2.1 instance through an independently versioned and hashed taxonomy profile.
11. In a successor owner path, load statements, reporting currency, fiscal dates, source population, close/live state, and snapshot provenance from PostgreSQL in one controlled boundary before issuing any authoritative report identity.

## Product principles

- Source systems describe economic events; Accounting determines their book treatment.
- Payment does not automatically equal revenue, and provider payout does not automatically equal cash posting.
- Every monetary result is reproducible from immutable source facts, versions, policy, mappings, report context, and taxonomy profile.
- Legal books are append-only. Corrections use reversals and replacement entries.
- External provider, regulator, filing, and bank identifiers remain behind adapters.
- Reporting layouts and taxonomies are versioned projections, not fixed columns in the journal core.
- A renderer, XBRL adapter, validator, or LLM is not a second accounting calculator.
- A content hash proves byte identity, not that a package originated from AIS-owned PostgreSQL.
- A caller-supplied package, currency, entity identifier, date range, snapshot reference, or Boolean flag can never create authoritative accounting-report truth.
- A well-formed XBRL document is not a claim of IFRS, DART, formula, certification, filing, or assurance status.
- Localized and model-generated explanations remain presentations or proposed interpretations; they cannot modify report facts or control results.

## Initial milestone

The first milestone is the proposal-to-trial-balance foundation:

- dependency-free executable reference core;
- PostgreSQL normalized schema;
- proposal, policy, and receipt contracts;
- tenant and period controls;
- exact-decimal, idempotency, reversal, and trial-balance tests;
- architecture, security, operability, and standards traceability.

## Financial-reporting proposal milestone

The first bounded reporting slice includes:

- a filing-independent, caller-supplied entity, currency, current period, optional comparison period, and decimal-precision context;
- a deterministic report **proposal** generated from a four-statement-shaped package;
- unconditional classification as `proposed`, `caller_supplied_statement_package`, `unverified`, and `authoritative_report=false`;
- a `financial_report_proposal` URN rather than an authoritative report URN;
- current and comparative profit-or-loss headlines;
- exact canonical facts for income statement, financial position, changes in equity, and cash flow;
- account-role facts for independently reviewed taxonomy and renderer profiles;
- statement identity, total, financial-position, equity, cash-flow, and cross-statement arithmetic controls;
- claimed source statement paths and snapshot references, source-package hash, and proposal-artifact hash;
- language-neutral explanation codes with exact parameters and evidence paths;
- a taxonomy-profile contract with version, namespace, entry point, package digest, concept mappings, and period types;
- deterministic XBRL 2.1 XML proposal generation with `validation=not_run` and `filing=not_ready`;
- explicit non-claims and successor requirements for database-owned source authority, official taxonomy profiles, independent validation, Inline XBRL, filing, and accessible rendering.

The slice deliberately excludes:

- an AIS owner command that proves database origin and publishes an authoritative report;
- an embedded IFRS Accounting Taxonomy or DART taxonomy;
- schema, linkbase, Formula, or Calculations 1.1 processing;
- regulator submission or acceptance;
- XBRL Certified Software claims;
- Inline XBRL, PDF, HTML, or spreadsheet rendering;
- report-run persistence and object-storage publication;
- unreviewed free-text commentary;
- consolidation, notes, segment disclosures, foreign currency, EPS, and jurisdiction-specific completeness.

## Authoritative publication milestone

A successor product slice must accept tenant, legal entity, book, fiscal period, purpose, and idempotency identity—not report amounts—and must obtain all accounting/reporting provenance from the AIS owner boundary. It must:

- authenticate and authorize tenant, actor, purpose, and decision context;
- load the four statements in one PostgreSQL `REPEATABLE READ` transaction;
- derive reporting currency and current/comparison dates from authoritative entity/book/calendar policy;
- retain source journal population, close/live state, knowledge cutoff, snapshot references, and package digest;
- classify a live or non-close population as provisional or reject it according to publication policy;
- persist report run, source, facts, artifact, validation, approval, publication, supersession, withdrawal, and outbox evidence under forced tenant isolation;
- issue an authoritative report identity only after required validation and maker-checker approval;
- retain historical reports and external receipts without rewriting them.

## Acceptance criteria

- Replaying an identical proposal produces one journal and the original receipt.
- Reusing an idempotency key with a different payload hash fails closed.
- An unbalanced proposal, unknown account role, mismatched tenant, closed period, or unsupported currency treatment produces no journal.
- Reversal retains the original journal and makes the scoped net balance zero for the test fixture.
- Equal statement package, report context, and taxonomy profile inputs produce equal proposal artifacts and byte-identical XBRL output.
- Every emitted proposal fact retains its statement type, period type, current/comparison context, exact decimal text, and source evidence path.
- Profit-or-loss, financial-position, equity, cash-flow, and cross-statement controls fail before any proposal can be exported.
- A caller cannot change a derived fact and obtain an XBRL instance by merely recomputing the report artifact hash.
- An arbitrary balanced package for an unrecorded tenant/entity and relabelled caller currency/dates remains `proposed`, `caller_supplied_statement_package`, `unverified`, and non-authoritative.
- The low-level builder never issues `urn:cwl:accounting:financial_report:{id}` or a filing-ready/validated status.
- An XBRL mapping fails when its canonical fact is absent or its duration/instant period type differs.
- No proposal-generation code fetches a taxonomy, executes active content, resolves external entities, or invokes an LLM.
- Every production statement and branch in the executable reference core and repository tooling is covered.
- All shipped public Python symbols have docstrings.
- All database schemas, tables, columns, policies, and functions follow the repository naming rule.
- Filing, certification, conformance, database-origin, approval, and publication claims remain false until the corresponding owner path, official profile, independent validation, jurisdiction fixture, maker-checker approval, and immutable release evidence exist.
