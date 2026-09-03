# Product Requirements Document

## Product outcome

Finance teams can receive accounting proposals from CWL products, resolve each proposal through approved accounting policy, post or hold it without duplication, reverse it without destroying history, prove every trial-balance amount back to source evidence, and generate snapshot-bound financial-report artifacts for exact-value presentation, reviewed explanation, and XBRL export.

## Primary users

- Controllers own accounting policy, chart-account mappings, books, close controls, reporting profiles, and report approval.
- Accounting operations review held and rejected proposals and perform approved reversals.
- Finance platform engineers operate proposal intake, posting, outbox, reconciliation, report generation, validation, publication, and evidence retention.
- Financial-report preparers review profit-or-loss movement, cross-statement controls, taxonomy mappings, explanations, and export validation evidence.
- Auditors trace a financial statement, report fact, XBRL fact, or trial-balance balance back through statement lines, journal lines, posting receipts, source proposals, source payload hashes, and report/taxonomy artifact identities.

## Core jobs

1. Accept a versioned `accounting_journal_proposal` from an approved source.
2. Detect exact replay versus conflicting reuse of an idempotency key.
3. Resolve tenant, legal entity, accounting book, fiscal period, currencies, account roles, and policy versions.
4. Post a balanced immutable journal or return a structured hold or rejection.
5. Reverse an original journal with an equal-and-opposite journal while preserving both.
6. Produce a trial balance that ties exactly to the included journal population.
7. Return an authoritative `accounting_posting_receipt` to the source system.
8. Load the four financial statements from one repeatable-read accounting package and produce one deterministic canonical report artifact without recalculating ledger truth.
9. Explain profit-or-loss movement and statement controls through exact parameters, status codes, and source evidence paths rather than unsupported prose.
10. Export mapped canonical facts as a deterministic XBRL 2.1 instance through an independently versioned and hashed taxonomy profile.

## Product principles

- Source systems describe economic events; Accounting determines their book treatment.
- Payment does not automatically equal revenue, and provider payout does not automatically equal cash posting.
- Every monetary result is reproducible from immutable source facts, versions, policy, mappings, report context, and taxonomy profile.
- Legal books are append-only. Corrections use reversals and replacement entries.
- External provider IDs and bank message fields remain behind adapters.
- Reporting layouts and taxonomies are versioned projections, not fixed columns in the journal core.
- Every report format consumes the same canonical snapshot-bound artifact; a renderer, XBRL adapter, or LLM is not a second accounting calculator.
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

## Financial-reporting milestone

The first bounded reporting slice includes:

- a filing-independent entity, currency, current period, optional comparison period, and decimal-precision context;
- a canonical report artifact generated only from the existing four-statement package;
- current and comparative profit-or-loss headlines;
- exact canonical facts for income statement, financial position, changes in equity, and cash flow;
- account-role facts for independently reviewed taxonomy and renderer profiles;
- statement identity, total, financial-position, equity, cash-flow, and cross-statement controls;
- source statement paths, snapshot references, source-package hash, and report-artifact hash;
- language-neutral explanation codes with exact parameters and evidence paths;
- a taxonomy-profile contract with version, namespace, entry point, package digest, concept mappings, and period types;
- deterministic XBRL 2.1 XML instance generation and instance digest;
- explicit non-claims and successor requirements for official taxonomy profiles, independent validation, Inline XBRL, filing, and accessible rendering.

The slice deliberately excludes:

- an embedded IFRS Accounting Taxonomy or DART taxonomy;
- schema, linkbase, Formula, or Calculations 1.1 processing;
- regulator submission or acceptance;
- XBRL Certified Software claims;
- Inline XBRL, PDF, HTML, or spreadsheet rendering;
- report-run persistence and object-storage publication;
- unreviewed free-text commentary;
- consolidation, notes, segment disclosures, foreign currency, EPS, and jurisdiction-specific completeness.

## Acceptance criteria

- Replaying an identical proposal produces one journal and the original receipt.
- Reusing an idempotency key with a different payload hash fails closed.
- An unbalanced proposal, unknown account role, mismatched tenant, closed period, or unsupported currency treatment produces no journal.
- Reversal retains the original journal and makes the scoped net balance zero for the test fixture.
- Equal statement package, report context, and taxonomy profile inputs produce equal report artifacts and byte-identical XBRL output.
- Every emitted report fact retains its statement type, period type, current/comparison context, exact decimal text, and source evidence path.
- Profit-or-loss, financial-position, equity, cash-flow, and cross-statement controls fail before any artifact can be exported.
- A caller cannot change a derived fact and obtain an XBRL instance by merely recomputing the report artifact hash.
- An XBRL mapping fails when its canonical fact is absent or its duration/instant period type differs.
- No report-generation code fetches a taxonomy, executes active content, resolves external entities, or invokes an LLM.
- Every production statement and branch in the executable reference core and repository tooling is covered.
- All shipped public Python symbols have docstrings.
- All database schemas, tables, columns, policies, and functions follow the repository naming rule.
- Filing, certification, and conformance claims remain false until the corresponding official profile, independent validation, jurisdiction fixture, approval, and release evidence exists.
