# Financial reporting and XBRL export design

**Date:** 2026-09-03  
**Status:** Approved for bounded proposal implementation; authoritative publication remains successor work  
**Owner:** `accounting-information-platform`

## Problem

The repository already owns trial balance and four financial-statement projections for one legal entity, accounting book, fiscal period, and optional comparison period. Buyers also need profit-or-loss headlines, report generation, report explanations, and XBRL export without allowing renderers, validators, or models to become another ledger.

The design must satisfy six needs:

1. expose a profit-or-loss summary from the existing income statement;
2. preserve the complete four-statement package and supplied source paths;
3. provide machine-readable explanations of movements and arithmetic controls;
4. export an XBRL 2.1 proposal through an externally supplied, versioned taxonomy profile;
5. prevent caller-supplied packages, dates, currencies, snapshot IDs, or hashes from being mistaken for AIS authority;
6. leave authoritative publication, statutory taxonomy conformance, taxonomy licensing, independent validation, Inline XBRL rendering, and jurisdiction submission to explicit owner-controlled gates.

## Existing authority

`PostgresPostingLedger.load_financial_statement_package` is the supported numerical source when an AIS owner command invokes it inside one PostgreSQL `REPEATABLE READ` transaction. It obtains income statement, statement of financial position, changes in equity, and cash-flow projections from AIS-owned facts.

The new pure functions do not invoke that method themselves. Therefore they cannot prove that their input package, entity, book, period, currency, dates, source population, or snapshot references came from AIS. Internal arithmetic consistency and content hashes do not establish origin.

Commercial usage, invoices, collections, and settlements remain upstream evidence owned by the Billing Control Plane. This repository alone decides their journal, account, period, close, and financial-reporting consequences after accepted posting evidence.

## Considered approaches

### Hard-code the IFRS Accounting Taxonomy in application code

Rejected. Taxonomy releases, entry points, local filing rules, extensions, labels, validation formulae, and licensing change independently of the ledger. Hard-coding concepts would couple financial truth to one taxonomy release and could silently claim conformance that has not been validated.

### Let each renderer or XBRL adapter query the ledger

Rejected. HTML, PDF, spreadsheet, narrative, and XBRL paths would independently reproduce signs, periods, comparisons, and formulas. That creates multiple numerical truths and allows snapshot tearing.

### Let a hash-backed pure function mint an authoritative report

Rejected. A caller can construct a perfectly balanced package for an unrecorded tenant/entity, supply any currency and dates, and compute a valid digest. SHA-256 proves content identity, not AIS-owned database origin.

### Build a separate reporting service immediately

Rejected for this slice. Proposal projection and serialization are deterministic and stateless. A network service would add deployment, authorization, and consistency failure modes before the contract is proven. The owner command remains in the Accounting Information Platform until scaling or security evidence justifies extraction.

### Unverified proposal formatter plus owner-controlled publication

Selected. The low-level package validates and serializes caller-supplied data but always emits a non-authoritative proposal. A later AIS owner command obtains statements and context from PostgreSQL, persists source provenance, runs independent validation, obtains maker-checker approval, and only then may issue authoritative report/publication identity.

## Domain boundary

```text
Caller-supplied package + report context
    |
    v
financial_reporting pure functions
    |
    +--> exact proposal facts and arithmetic controls
    +--> structured explanation proposal
    +--> taxonomy-profile-driven XBRL 2.1 proposal
    |
    v
proposed / caller_supplied_statement_package / unverified

Authenticated owner command (successor)
    |
    v
PostgreSQL REPEATABLE READ
    |- legal entity / book / fiscal period / currency / dates
    |- journal or close-snapshot population
    |- four financial statements
    |- close/live state and knowledge cutoff
    v
owner-bound report run and artifact
    v
independent validation -> maker-checker approval -> publication receipt
```

The new package is `accounting_information_platform.financial_reporting`. It has no database adapter, network client, file writer, model-provider credential, or authority-elevation parameter.

## Low-level proposal classification

`build_financial_report_artifact` unconditionally returns:

```text
truth_status_code = proposed
source_authority_code = caller_supplied_statement_package
publication_readiness_code = unverified
authoritative_report = false
report_artifact_reference = urn:cwl:accounting:financial_report_proposal:{sha256}
```

`export_xbrl_instance` preserves those values and additionally returns:

```text
xbrl_validation_status_code = not_run
filing_readiness_code = not_ready
authoritative_report = false
```

No caller-controlled value can change these fields.

## Report context

`FinancialReportContext` carries caller-supplied filing context:

- entity identifier scheme and identifier;
- reporting currency;
- current period start and end dates;
- optional comparison period dates;
- XBRL decimal precision.

Dates must be complete pairs and ranges must be ordered. These validations prevent malformed XML contexts but do not prove the values match AIS master/calendar data. The successor owner command must derive them from authoritative legal-entity, book, fiscal-calendar, and reporting-policy facts.

## Proposal facts and controls

The proposal emits exact-decimal fact records with:

- `fact_code`;
- `fact_amount` as canonical decimal text;
- `period_context_code` (`current` or `comparison`);
- `statement_type_code`;
- `period_type_code`;
- `source_evidence_paths`.

Summary facts include revenue, expense, net income, assets, liabilities, equity, and unclosed net income. Supplied statement lines are also aggregated by account role so a later reviewed profile can map role-based facts without binding journal logic to one taxonomy.

The builder fails closed when:

- a required statement is missing;
- identity differs within the supplied package;
- a monetary value is malformed, non-finite, negative where prohibited, or two-sided;
- line totals disagree with statement totals;
- revenue minus expense differs from net income;
- assets differ from liabilities plus equity plus unclosed net income;
- changes in equity does not roll forward or tie to supplied income/financial position;
- cash flow does not reconcile operations, activities, opening/closing cash, supplied net income, or the supplied `cash_receipt` role;
- comparison data, identity, or context dates are incomplete.

These controls prove internal consistency of the supplied package only.

## Structured report explanation

The first explanation contract is deterministic and language-neutral. It returns message codes, exact parameters, direction codes, status codes, and source paths instead of free prose. Initial records cover:

- current profit-or-loss composition;
- prior-period net-income change when comparative information is present;
- statement-of-financial-position equation status;
- changes-in-equity rollforward status;
- cash-flow rollforward status.

It does not explain an unobserved business cause. A localized renderer may resolve message codes through the organization’s versioned translation resource. A Contextual Orchestrator operation may later produce narrative commentary only from an owner-bound evidence bundle, with model/prompt/tool provenance, unsupported-claim rejection, and human approval before external publication.

## XBRL taxonomy profile

`XbrlTaxonomyProfile` contains:

- profile identifier and version;
- reporting-standard code and taxonomy release code;
- taxonomy namespace URI and XML prefix;
- schema entry-point URI;
- immutable taxonomy-package SHA-256 digest;
- one or more `XbrlConceptMapping` records.

Each mapping connects one proposal fact code to one taxonomy concept local name and declares `duration` or `instant` period type. The implementation ships no IFRS, DART, or other jurisdiction profile. Profiles must be reviewed and released separately from licensed official packages. A valid profile does not prove source authority or taxonomy conformance.

## XBRL proposal output

The serializer produces deterministic XBRL 2.1 XML with:

- one duration and one instant context for the current period;
- optional comparison contexts;
- one ISO 4217 currency unit;
- a schema reference to the supplied profile entry point;
- mapped monetary facts with explicit `contextRef`, `unitRef`, and `decimals`;
- a canonical SHA-256 digest and explicit unverified/filing-not-ready metadata.

Before serialization it verifies source/proposal hashes and rebuilds the complete proposal from the embedded source package, preventing derived-fact mutation followed by outer-hash recomputation.

XML is constructed with the Python standard library. The implementation never parses a taxonomy, expands external entities, resolves a DTD, fetches a schema, or executes active content.

## Authoritative owner path

The successor command must accept tenant/entity/book/period/purpose/profile identifiers and idempotency evidence, never report amounts. In one controlled flow it must:

1. verify authenticated tenant, actor, purpose, decision, and resource access;
2. derive reporting currency and date ranges from AIS-owned facts;
3. load the four statements and source population under PostgreSQL `REPEATABLE READ`;
4. retain journal or hard-close snapshot population, close/live state, knowledge cutoff, and package hash;
5. classify live/non-close reports as provisional or reject publication by policy;
6. persist report run, source, proposal, artifact, and outbox atomically under forced tenant isolation;
7. bind official taxonomy profile and independent validation results;
8. obtain maker-checker approval;
9. issue authoritative report/publication identity and external receipt only after required gates pass.

A Boolean, hash, signed profile, database-looking identifier, test fixture, or in-memory ledger cannot substitute for this path.

## Compatibility and conformance claims

This slice creates an unverified XBRL 2.1 proposal envelope. It does not claim:

- AIS database origin;
- IFRS Accounting Taxonomy conformance;
- DART filing acceptance;
- XBRL Certified Software status;
- Calculations 1.1 validation;
- Formula validation;
- Inline XBRL generation;
- taxonomy-extension authoring;
- approval, publication, filing, audit, or assurance.

Those claims require an owner-bound source population, pinned official taxonomy package, independent validating processor, jurisdiction fixtures, calculation/formula results, maker-checker approval, and immutable release evidence.

## Delivery slices

### Implemented in this branch

- deterministic non-authoritative proposal construction;
- unconditional truth/source/readiness classification;
- exact profit-or-loss summary and comparative movement;
- supplied-package arithmetic and cross-statement controls;
- structured explanation records;
- injected taxonomy profile;
- deterministic XBRL 2.1 proposal serialization;
- full proposal reconstruction before export;
- tests for arbitrary balanced relabelled input remaining unverified;
- public package exports;
- ADR, standards traceability, and product/test documentation.

### Prepared as successor work

- AIS owner report command and source-authority receipt;
- 3NF report/taxonomy/validation/approval/publication registries;
- controlled object-storage publication;
- official IFRS Accounting Taxonomy and DART profile packages;
- independent XBRL/Calculations/Formula/jurisdiction validation;
- Inline XBRL 1.1 and accessible HTML/PDF/spreadsheet generation;
- signed report packages;
- localized management commentary and approval workflow;
- regulator/customer delivery, rejection, supersession, and withdrawal.

## Acceptance criteria

- No financial amount is computed with binary floating point.
- Same supplied package, context, and taxonomy profile produce byte-identical proposals.
- Every emitted fact retains supplied source paths.
- Unknown or inconsistent supplied accounting data fails before XML generation.
- Taxonomy mappings cannot reference missing facts or disagree with canonical period type.
- XML contains no external content fetched at runtime.
- Arbitrary balanced supplied data never receives authoritative report identity or readiness.
- XBRL output always reports validation not run and filing not ready.
- Production statement coverage, branch coverage, and public API docstrings remain 100% on the exact head before merge.
