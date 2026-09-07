# ADR 0067: Financial reporting separates proposal serialization from authoritative publication

- Status: Proposed
- Date: 2026-09-03
- Bounded context: Accounting Reporting
- Depends on: ADR 0021 financial-statement read boundary, ADR 0032 changes in equity, ADR 0033 cash flow, and ADR 0037 four-statement package snapshot
- Supersedes: none

## Problem

The Accounting Information Platform already produces an income statement, statement of financial position, statement of changes in equity, and cash-flow statement from posted journals and period-close evidence. ADR 0037 binds those statements to one PostgreSQL `REPEATABLE READ` snapshot. Buyers also need a durable profit-or-loss view, report generation, structured report explanations, and XBRL export.

Those needs introduce three material risks.

First, a renderer or XBRL adapter could become a second accounting calculator. If it re-queries journals, changes signs, maps account roles with local formulas, or reads statements at different times, the output can diverge from the authoritative financial-statement package.

Second, embedding one filing taxonomy in the ledger would falsely couple accounting truth to a taxonomy release, jurisdiction, filing entry point, extension policy, license, and validation tool. A syntactically well-formed XBRL document is not evidence of IFRS Accounting Taxonomy conformance, DART acceptance, formula validity, or XBRL Certified Software status.

Third, a content hash proves byte identity, not origin or authority. A pure function that accepts an arbitrary balanced mapping and caller-supplied entity, currency, and dates cannot mint an authoritative AIS report. Otherwise a caller could relabel synthetic or stale data and receive an accounting-shaped URN and XBRL instance even though PostgreSQL never owned the source population.

## Constraints

- Posted journals, chart-account roles, periods, close evidence, and database-owned report provenance remain authoritative in this repository.
- The existing four-statement package is the sole numerical input to report projection.
- Billing usage, pricing, invoice, payment, refund, dispute, and settlement facts remain foreign commercial evidence until accepted through accounting journals.
- Every monetary value uses exact `Decimal` semantics and canonical decimal text.
- A report proposal must retain claimed statement snapshot references, source paths, source hashes, and its own deterministic digest.
- Taxonomy releases and mappings must be versioned independently of the ledger and must identify the source package by an immutable SHA-256 digest.
- The first slice must not fetch schemas, parse DTDs, resolve external entities, execute active content, or use a model provider.
- Human-readable localization is a presentation concern and must not change report facts.
- No caller flag, arbitrary SHA-shaped reference, caller-supplied snapshot ID, report context, or taxonomy assertion may elevate a proposal to authoritative truth.

## Considered alternatives

### Hard-code the IFRS Accounting Taxonomy in application code

Rejected. The IFRS Accounting Taxonomy, local filing taxonomies, entry points, labels, validation formulae, and filing rules change independently. Hard-coding concepts would require ledger releases for taxonomy changes and could silently turn an unreviewed mapping into a filing claim.

### Let each report renderer query the ledger

Rejected. HTML, PDF, spreadsheet, narrative, and XBRL renderers would independently reproduce signs, periods, comparisons, and cross-statement checks. That creates multiple numerical truths and allows snapshot tearing.

### Let a pure builder mint authoritative report identity

Rejected. Arithmetic consistency plus a SHA-256 digest does not prove that the tenant, legal entity, book, period, currency, report dates, source population, or snapshot references came from AIS-owned PostgreSQL. A pure builder cannot distinguish a real read from a caller-crafted mapping.

### Create a network reporting service immediately

Rejected for this slice. The initial projection and serialization operations are deterministic and stateless. A separate service would add authentication, availability, deployment, and consistency failure modes before the artifact contract is proven. The owner command remains an application/persistence boundary in this repository until scaling or security evidence justifies extraction.

### Unverified canonical proposal plus later owner-controlled publication

Accepted. A pure operation can validate and canonicalize one four-statement-shaped package for testing, adapter development, report preparation, and format generation, but its output is always `proposed`, `caller_supplied_statement_package`, and `unverified`. It uses the `financial_report_proposal` URN namespace and can never be elevated through caller input.

An authoritative report requires a separate AIS owner command that accepts tenant/entity/book/period/purpose identifiers, obtains the statements, currency, dates, and snapshot provenance from PostgreSQL in one controlled boundary, persists the source population and report run, and issues an append-only authoritative identity only after required validation and approval. That owner path is successor work and is not simulated by this pure function.

## Decision

Add the `accounting_information_platform.financial_reporting` package with five public low-level contracts:

- `FinancialReportContext`
- `XbrlConceptMapping`
- `XbrlTaxonomyProfile`
- `build_financial_report_artifact`
- `export_xbrl_instance`

The two operations are format/projection primitives. Their public output is explicitly non-authoritative until consumed by the later owner-controlled report-run boundary.

### Canonical report proposal

`build_financial_report_artifact` accepts one caller-supplied financial-statement package and one caller-supplied report context. It:

1. canonicalizes the source package as sorted UTF-8 JSON;
2. verifies the common tenant, legal entity, book, fiscal period, scope, and comparison identity of all four statements;
3. verifies line totals and exact profit-or-loss arithmetic;
4. verifies assets = liabilities + equity + unclosed net income;
5. verifies the changes-in-equity rollforward and income-statement tie;
6. verifies cash-from-operations, net-cash-change, opening/closing cash, income-statement tie, and the claimed `cash_receipt` role when present;
7. creates current and optional comparison facts with exact amount text, period type, statement type, and source evidence paths;
8. creates a deterministic profit-or-loss summary and structured explanation records;
9. retains the complete canonical source statement package;
10. emits source-package and report-artifact SHA-256 identities;
11. unconditionally emits:

```text
truth_status_code = proposed
source_authority_code = caller_supplied_statement_package
publication_readiness_code = unverified
authoritative_report = false
report_artifact_reference = urn:cwl:accounting:financial_report_proposal:{sha256}
```

The artifact contains no current clock, random identifier, mutable label, provider response, or binary floating-point calculation. Equal inputs produce byte-equivalent canonical data. Its claimed tenant, entity, book, period, currency, dates, and snapshot references are not authority evidence.

### Structured report explanation

The first explanation contract is language-neutral. It contains:

- `explanation_code`
- `status_code`
- `direction_code`
- exact `parameter_map`
- `source_evidence_paths`

It covers current profit-or-loss composition, comparative net-income movement, financial-position equation status, changes-in-equity rollforward status, and cash-flow rollforward status. It does not generate free prose or explain unobserved business causes.

A later localized renderer resolves message codes through the versioned translation resource. A later Contextual Orchestrator operation may draft management commentary only from an owner-bound evidence bundle. Such commentary must retain model, provider, prompt, evidence, verification, locale, approval, and publication provenance. It cannot change facts or become authoritative without review.

### XBRL taxonomy profile

`XbrlTaxonomyProfile` identifies:

- profile identifier and version;
- reporting-standard and taxonomy-release codes;
- XML prefix and taxonomy namespace URI;
- schema entry-point URI;
- taxonomy-package SHA-256 digest;
- one-to-one canonical-fact mappings with taxonomy concept local name and `duration` or `instant` period type.

The application ships no statutory taxonomy profile in this slice. IFRS, DART, or another jurisdiction profile must be created from the licensed official package, reviewed independently, released immutably, and validated against jurisdiction fixtures.

### XBRL proposal export

`export_xbrl_instance`:

1. verifies the source-package hash and report-artifact hash;
2. rehydrates and validates the caller-supplied report context;
3. rebuilds the entire proposal from its embedded source package and compares it exactly, preventing a caller from modifying derived facts and merely recomputing the outer hash;
4. rejects absent mappings and mapping/fact period-type disagreement;
5. emits XBRL 2.1 entity, duration/instant context, ISO 4217 unit, schema reference, and monetary fact elements;
6. preserves the proposal classification and unconditionally returns:

```text
truth_status_code = proposed
publication_readiness_code = unverified
authoritative_report = false
xbrl_validation_status_code = not_run
filing_readiness_code = not_ready
```

7. returns the XML media type, proposal-specific deterministic file name, taxonomy provenance, report-proposal identity, and XML SHA-256 digest.

No taxonomy, linkbase, schema, formula, or external entity is retrieved while generating the instance. An authoritative publication command must not infer authority from this output; it must replace the caller-supplied source boundary with database-owned provenance and retain the exact proposal as input evidence.

## Authoritative owner-path requirement

The successor AIS command must prove all of the following in one controlled flow:

1. the authenticated/purpose-bound caller selected an accessible tenant, legal entity, accounting book, and fiscal period;
2. PostgreSQL loaded the four statements under one `REPEATABLE READ` transaction;
3. report currency came from the authoritative legal entity/book/reporting policy, not request data;
4. current/comparison dates came from authoritative fiscal-period/calendar facts;
5. source journal population, close/live status, statement snapshot IDs, knowledge cutoff, and exact package hash were retained;
6. a live/non-close package is explicitly provisional or rejected by publication policy;
7. the report run, source, artifact, validation, approval, outbox, and publication receipt are tenant-scoped and append-only;
8. only that boundary may issue an authoritative report identity, and it may do so only after its required validation and approval gates.

A test-only in-memory implementation, a request header, a Boolean `authoritative` parameter, an arbitrary database-looking ID, or a signed taxonomy profile cannot satisfy this requirement.

## Standards baseline

The format contract uses the following published specifications:

- XBRL International. (2003). *Extensible Business Reporting Language (XBRL) 2.1—Recommendation, corrected errata through 2013*.
- XBRL International. (2021). *Open Information Model 1.0* for future xBRL-JSON and xBRL-CSV projections.
- XBRL International. (2023). *Calculations 1.1* as a required successor validation gate, not an implemented claim in this slice.
- XBRL International. (2026). *Inline XBRL 1.1* as the human-readable XBRL successor format, not an implemented claim in this slice.
- IFRS Foundation. (2025). *IFRS Accounting Taxonomy 2025* as the current published IFRS taxonomy available for 2026 reporting; the platform does not bundle or claim conformance to it here.

Project Tavi and Taxonomy Packages 1.1 were public working drafts when this decision was recorded. They are monitored successors and do not replace the format contracts above until formally released and adopted through a later ADR.

## Consequences

### Positive

- All candidate report formats consume the same internally consistent exact-value proposal.
- Profit-or-loss, financial-position, equity, and cash-flow arithmetic controls fail before rendering.
- An arbitrary balanced package cannot acquire an authoritative AIS report identity through the pure builder.
- Taxonomy releases can change without changing journals or account-role calculations.
- Exact facts remain usable by HTML, PDF, spreadsheet, XBRL, APIs, tests, and verified narrative workflows.
- Every exported fact retains a route back to the supplied statement line or total that produced it.
- XBRL proposal output is deterministic, replayable, and bound to an immutable taxonomy-package identity.

### Negative

- The current public operations cannot by themselves produce a buyer-authoritative or filing-ready report.
- A database-owned report-run command and normalized provenance model remain required.
- A taxonomy profile must be curated and released before a statutory concept can be emitted.
- This slice does not provide presentation linkbases, labels, dimensions, extension taxonomy authoring, formula evaluation, or filing submission.
- The complete source package retained inside the proposal increases artifact size; object-storage persistence and retention rules must account for that.
- Free-text management commentary remains a separate reviewed workflow.

## Non-claims

This ADR and implementation do not claim:

- authoritative AIS report origin from the pure builder;
- IFRS Accounting Taxonomy conformance;
- DART filing acceptance;
- XBRL Certified Software status;
- Calculations 1.1 validation;
- Formula validation;
- Inline XBRL generation;
- statutory financial-statement completeness;
- audit opinion or assurance.

## Required successor work

1. Add a high-level purpose-bound report-run command that loads statements, currency, dates, source population, close/live state, and snapshot provenance from PostgreSQL under one owner-controlled transaction.
2. Add 3NF registries for taxonomy profiles, mappings, report runs, sources, artifacts, validation receipts, explanations, approvals, publication receipts, supersession, and withdrawal.
3. Publish immutable artifacts to controlled object storage with tenant encryption, retention, legal hold, content verification, and non-overwrite receipts.
4. Release reviewed IFRS Accounting Taxonomy and DART profiles from official package digests without redistributing licensed content improperly.
5. Validate every candidate instance with an independent XBRL processor and retain XBRL 2.1, Calculations 1.1, Formula, taxonomy-package, and jurisdiction results.
6. Add Inline XBRL 1.1, accessible HTML, PDF, and spreadsheet renderers from the same owner-bound artifact.
7. Add localized explanation rendering for Korean, English, Japanese, Chinese, Vietnamese, Spanish, German, and French, including missing/unsupported explanation states.
8. Add Contextual Orchestrator interpreter/verifier operations with evidence-only inputs, unsupported-claim rejection, human approval, and publication provenance.
9. Add statement-note, dimension, consolidation, segment, foreign-currency, EPS, and disclosure support through separate reviewed accounting decisions.
10. Add signed report packages, SBOM/provenance, retention, legal-hold, supersession, withdrawal, and regulator/customer delivery workflows.
11. Add Figma and Storybook states only after API and artifact state transitions are fixed; include normal, loading, empty, validation failure, permission denied, superseded, partial comparison, unverified proposal, and print/export states.

## Verification

The branch must demonstrate:

- arbitrary balanced caller data remains a proposed, caller-supplied, unverified report and XBRL output;
- the pure builder never issues an authoritative report URN or readiness claim;
- deterministic current and comparative proposal artifacts;
- exact fact/evidence links;
- all four statement equations and cross-statement ties;
- source and derived-artifact tamper rejection;
- taxonomy identity, digest, namespace, concept, and period-type validation;
- deterministic current and comparative XBRL contexts and facts;
- production statement and branch coverage of 100%;
- public API docstrings of 100%;
- exact-head repository, SAST, security, dependency, packaging, SBOM, and provenance checks before merge.
