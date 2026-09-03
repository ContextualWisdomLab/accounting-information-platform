# ADR 0067: Financial reports use canonical artifacts and injected XBRL taxonomy profiles

- Status: Proposed
- Date: 2026-09-03
- Bounded context: Accounting Reporting
- Depends on: ADR 0021 financial-statement read boundary, ADR 0032 changes in equity, ADR 0033 cash flow, and ADR 0037 four-statement package snapshot
- Supersedes: none

## Problem

The Accounting Information Platform already produces an income statement, statement of financial position, statement of changes in equity, and cash-flow statement from posted journals and period-close evidence. ADR 0037 binds those statements to one PostgreSQL `REPEATABLE READ` snapshot. Buyers also need a durable profit-or-loss view, report generation, structured report explanations, and XBRL export.

Those needs introduce two material risks.

First, a renderer or XBRL adapter could become a second accounting calculator. If it re-queries journals, changes signs, maps account roles with local formulas, or reads statements at different times, the output can diverge from the authoritative financial-statement package.

Second, embedding one filing taxonomy in the ledger would falsely couple accounting truth to a taxonomy release, jurisdiction, filing entry point, extension policy, license, and validation tool. A syntactically well-formed XBRL document is not evidence of IFRS Accounting Taxonomy conformance, DART acceptance, formula validity, or XBRL Certified Software status.

## Constraints

- Posted journals, chart-account roles, periods, and close evidence remain authoritative in this repository.
- The existing four-statement package is the sole numerical input to report generation.
- Billing usage, pricing, invoice, payment, refund, dispute, and settlement facts remain foreign commercial evidence until accepted through accounting journals.
- Every monetary value uses exact `Decimal` semantics and canonical decimal text.
- A report artifact must retain statement snapshot references, source paths, source hashes, and its own deterministic digest.
- Taxonomy releases and mappings must be versioned independently of the ledger and must identify the source package by an immutable SHA-256 digest.
- The first slice must not fetch schemas, parse DTDs, resolve external entities, execute active content, or use a model provider.
- Human-readable localization is a presentation concern and must not change report facts.

## Considered alternatives

### Hard-code the IFRS Accounting Taxonomy in application code

Rejected. The IFRS Accounting Taxonomy, local filing taxonomies, entry points, labels, validation formulae, and filing rules change independently. Hard-coding concepts would require ledger releases for taxonomy changes and could silently turn an unreviewed mapping into a filing claim.

### Let each report renderer query the ledger

Rejected. HTML, PDF, spreadsheet, narrative, and XBRL renderers would independently reproduce signs, periods, comparisons, and cross-statement checks. That creates multiple numerical truths and allows snapshot tearing.

### Create a network reporting service immediately

Rejected for this slice. The initial operation is deterministic and stateless. A separate service would add authentication, availability, deployment, and consistency failure modes before the contract is proven. The module remains extractable when persistence, rendering queues, jurisdiction submission, or materially different scaling justifies it.

### Canonical report artifact plus injected taxonomy profile

Accepted. The existing four-statement package is converted once into a canonical exact-value artifact. Presentation and XBRL adapters consume that artifact rather than the ledger. Taxonomy identity and canonical-fact mappings arrive through an independently versioned, immutable profile.

## Decision

Add the `accounting_information_platform.financial_reporting` package with five public contracts:

- `FinancialReportContext`
- `XbrlConceptMapping`
- `XbrlTaxonomyProfile`
- `build_financial_report_artifact`
- `export_xbrl_instance`

### Canonical report artifact

`build_financial_report_artifact` accepts one financial-statement package and one report context. It:

1. canonicalizes the source package as sorted UTF-8 JSON;
2. verifies the common tenant, legal entity, book, fiscal period, scope, and comparison identity of all four statements;
3. verifies line totals and exact profit-or-loss arithmetic;
4. verifies assets = liabilities + equity + unclosed net income;
5. verifies the changes-in-equity rollforward and income-statement tie;
6. verifies cash-from-operations, net-cash-change, opening/closing cash, income-statement tie, and the authoritative `cash_receipt` role when present;
7. creates current and optional comparison facts with exact amount text, period type, statement type, and source evidence paths;
8. creates a deterministic profit-or-loss summary and structured explanation records;
9. retains the complete canonical source statement package;
10. emits source-package and report-artifact SHA-256 identities.

The artifact contains no current clock, random identifier, mutable label, provider response, or binary floating-point calculation. Equal inputs produce byte-equivalent canonical data.

### Structured report explanation

The first explanation contract is language-neutral. It contains:

- `explanation_code`
- `status_code`
- `direction_code`
- exact `parameter_map`
- `source_evidence_paths`

It covers current profit-or-loss composition, comparative net-income movement, financial-position equation status, changes-in-equity rollforward status, and cash-flow rollforward status. It does not generate free prose.

A later localized renderer resolves message codes through the versioned translation resource. A later Contextual Orchestrator operation may draft management commentary only from this evidence bundle. Such commentary must retain model, provider, prompt, evidence, verification, locale, approval, and publication provenance. It cannot change facts or become authoritative without review.

### XBRL taxonomy profile

`XbrlTaxonomyProfile` identifies:

- profile identifier and version;
- reporting-standard and taxonomy-release codes;
- XML prefix and taxonomy namespace URI;
- schema entry-point URI;
- taxonomy-package SHA-256 digest;
- one-to-one canonical-fact mappings with taxonomy concept local name and `duration` or `instant` period type.

The application ships no statutory taxonomy profile in this slice. IFRS, DART, or another jurisdiction profile must be created from the licensed official package, reviewed independently, released immutably, and validated against jurisdiction fixtures.

### XBRL instance export

`export_xbrl_instance`:

1. verifies the source-package hash and report-artifact hash;
2. rehydrates and validates the report context;
3. rebuilds the entire artifact from its embedded source package and compares it exactly, preventing a caller from modifying derived facts and merely recomputing the outer hash;
4. rejects absent mappings and mapping/fact period-type disagreement;
5. emits XBRL 2.1 entity, duration/instant context, ISO 4217 unit, schema reference, and monetary fact elements;
6. returns the XML media type, deterministic file name, taxonomy provenance, report-artifact identity, and XML SHA-256 digest.

No taxonomy, linkbase, schema, formula, or external entity is retrieved while generating the instance.

## Standards baseline

The production contract uses the following published specifications:

- XBRL International. (2003). *Extensible Business Reporting Language (XBRL) 2.1—Recommendation, corrected errata through 2013*.
- XBRL International. (2021). *Open Information Model 1.0* for future xBRL-JSON and xBRL-CSV projections.
- XBRL International. (2023). *Calculations 1.1* as a required successor validation gate, not an implemented claim in this slice.
- XBRL International. (2026). *Inline XBRL 1.1* as the human-readable XBRL successor format, not an implemented claim in this slice.
- IFRS Foundation. (2025). *IFRS Accounting Taxonomy 2025* as the current published IFRS taxonomy available for 2026 reporting; the platform does not bundle or claim conformance to it here.

Project Tavi and Taxonomy Packages 1.1 were public working drafts when this decision was recorded. They are monitored successors and do not replace the production contracts above until formally released and adopted through a later ADR.

## Consequences

### Positive

- All report formats consume the same snapshot-bound accounting facts.
- Profit-or-loss, financial-position, equity, and cash-flow controls fail before rendering.
- Taxonomy releases can change without changing journals or account-role calculations.
- Exact facts remain usable by HTML, PDF, spreadsheet, XBRL, APIs, and verified narrative workflows.
- Every exported fact retains a route back to the statement line or total that produced it.
- XBRL output is deterministic, replayable, and bound to an immutable taxonomy-package identity.

### Negative

- A taxonomy profile must be curated and released before a statutory concept can be emitted.
- This slice does not provide presentation linkbases, labels, dimensions, extension taxonomy authoring, formula evaluation, or filing submission.
- The complete source package retained inside the artifact increases artifact size; object-storage persistence and retention rules must account for that.
- Free-text management commentary remains a separate reviewed workflow.

## Non-claims

This ADR and implementation do not claim:

- IFRS Accounting Taxonomy conformance;
- DART filing acceptance;
- XBRL Certified Software status;
- Calculations 1.1 validation;
- Formula validation;
- Inline XBRL generation;
- statutory financial-statement completeness;
- audit opinion or assurance.

## Required successor work

1. Add 3NF registries for taxonomy profiles, mappings, report runs, artifacts, validation receipts, explanations, approvals, and publication receipts.
2. Add purpose-bound HTTP commands that load the existing financial-statement package inside the authoritative accounting context and publish immutable artifacts to object storage.
3. Release reviewed IFRS Accounting Taxonomy and DART profiles from official package digests without redistributing licensed content improperly.
4. Validate every candidate instance with an independent XBRL processor and retain XBRL 2.1, Calculations 1.1, Formula, taxonomy-package, and jurisdiction results.
5. Add Inline XBRL 1.1, accessible HTML, PDF, and spreadsheet renderers from the same artifact.
6. Add localized explanation rendering for Korean, English, Japanese, Chinese, Vietnamese, Spanish, German, and French, including missing/unsupported explanation states.
7. Add Contextual Orchestrator interpreter/verifier operations with evidence-only inputs, unsupported-claim rejection, human approval, and publication provenance.
8. Add statement-note, dimension, consolidation, segment, foreign-currency, EPS, and disclosure support through separate reviewed accounting decisions.
9. Add signed report packages, SBOM/provenance, retention, legal-hold, supersession, and withdrawal workflows.
10. Add Figma and Storybook states only after API and artifact state transitions are fixed; include normal, loading, empty, validation failure, permission denied, superseded, partial comparison, and print/export states.

## Verification

The branch must demonstrate:

- deterministic current and comparative artifacts;
- exact fact/evidence links;
- all four statement equations and cross-statement ties;
- source and derived-artifact tamper rejection;
- taxonomy identity, digest, namespace, concept, and period-type validation;
- deterministic current and comparative XBRL contexts and facts;
- production statement and branch coverage of 100%;
- public API docstrings of 100%;
- exact-head repository, SAST, security, dependency, packaging, SBOM, and provenance checks before merge.
