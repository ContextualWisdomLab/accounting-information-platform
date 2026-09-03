# Financial reporting and XBRL export design

**Date:** 2026-09-03  
**Status:** Approved for bounded implementation  
**Owner:** `accounting-information-platform`

## Problem

The repository already owns the trial balance and the four financial-statement projections for one legal entity, accounting book, fiscal period, and optional comparison period. It does not yet produce a durable, deterministic report artifact that can be handed to a renderer, an XBRL processor, or an explanation workflow without allowing those consumers to recalculate accounting truth.

A production design must satisfy five needs without creating another ledger:

1. expose a profit-or-loss summary from the existing income statement;
2. preserve the complete four-statement package and its snapshot provenance;
3. provide machine-readable, evidence-linked explanations of movements and control checks;
4. export an XBRL 2.1 instance through an externally supplied, versioned taxonomy profile;
5. leave statutory filing conformance, taxonomy licensing, Inline XBRL rendering, and jurisdiction submission to explicit later gates.

## Existing authority

`PostgresPostingLedger.load_financial_statement_package` remains the sole numerical source. It obtains income statement, statement of financial position, changes in equity, and cash-flow projections inside one PostgreSQL `REPEATABLE READ` transaction. This feature must not query journals independently, modify posted facts, or introduce a second statement formula.

Commercial usage, invoices, collections, and settlements remain upstream evidence owned by the Billing Control Plane. This repository alone decides their journal, account, period, close, and financial-reporting consequences.

## Considered approaches

### Hard-code the IFRS Accounting Taxonomy in application code

Rejected. Taxonomy releases, entry points, local filing rules, extensions, labels, and licensing change independently of the ledger. A hard-coded concept list would couple financial truth to one taxonomy release and could silently claim conformance that has not been validated.

### Build a separate reporting service immediately

Rejected for this slice. The first implementation is deterministic, stateless, and small. A network service would add deployment, authorization, and consistency failure modes before the contract is proven. The boundary remains extractable if artifact persistence, rendering queues, or jurisdiction submission later justify an independent service.

### Canonical report artifact plus injected taxonomy profile

Selected. The accounting package is converted once into a canonical report artifact containing exact decimal facts, source paths, source hashes, report context, and structured explanation records. XBRL serialization consumes that artifact and a caller-supplied taxonomy profile whose identity, schema entry point, namespace, package digest, and fact mappings are explicit.

## Domain boundary

```text
Posted journals and close snapshots
              |
              v
Existing financial-statement package
              |
              v
Canonical financial report artifact
       |                     |
       v                     v
Structured explanation      XBRL taxonomy profile
records                      + XBRL 2.1 serializer
       |                     |
       v                     v
Localized renderer or       XBRL instance + artifact digest
verified LLM interpreter     + validation status outside this slice
```

The new module is `accounting_information_platform.financial_reporting`. It has no database adapter, network client, file writer, or model-provider credential.

## Canonical report context

`FinancialReportContext` carries filing-independent context:

- entity identifier scheme and identifier;
- reporting currency;
- current period start and end dates;
- optional comparison period dates;
- XBRL decimal precision.

The context is caller-supplied because the current statement package exposes fiscal-period identity but not legal filing identifiers or entity schemes. Dates must be complete pairs and ranges must be ordered.

## Canonical facts

The artifact emits exact-decimal fact records with:

- `fact_code`;
- `fact_amount` as canonical decimal text;
- `period_context_code` (`current` or `comparison`);
- `statement_type_code`;
- `source_evidence_paths`.

Summary facts include revenue, expense, net income, assets, liabilities, equity, and the balance-sheet unclosed net-income amount. Statement-line facts are also aggregated by account role so a jurisdiction profile can map an authoritative accounting role to a taxonomy concept without binding the ledger to that taxonomy.

The artifact fails closed when:

- the four required statements are missing;
- a monetary value is malformed or non-finite;
- an account class or statement line shape is unknown;
- income-statement arithmetic does not reproduce `net_income_amount`;
- the statement of financial position does not satisfy assets = liabilities + equity + unclosed net income;
- comparison data is present without comparison dates;
- comparison identity is inconsistent across statements.

## Structured report explanation

The first explanation contract is deterministic and language-neutral. It returns message codes, exact parameters, direction codes, and source evidence paths instead of free prose. Initial records cover:

- current profit-or-loss composition;
- prior-period net-income change when comparative information is present;
- statement-of-financial-position equation status;
- changes-in-equity rollforward status;
- cash-flow rollforward status.

A localized renderer may resolve message codes through the organization’s versioned translation resource. A Contextual Orchestrator operation may later produce narrative commentary, but only from this evidence bundle, with model and prompt provenance, unsupported-claim rejection, and human approval before external publication.

## XBRL taxonomy profile

`XbrlTaxonomyProfile` contains:

- profile identifier and version;
- reporting-standard code and taxonomy release code;
- taxonomy namespace URI and XML prefix;
- schema entry-point URI;
- immutable taxonomy-package SHA-256 digest;
- one or more `XbrlConceptMapping` records.

Each mapping connects one canonical fact code to one taxonomy concept local name and declares `duration` or `instant` period type. The implementation does not ship an IFRS, DART, or other jurisdiction profile. Such profiles must be reviewed and released separately from licensed official packages.

## XBRL output

The serializer produces a deterministic XBRL 2.1 XML instance with:

- one duration and one instant context for the current period;
- optional comparison contexts;
- one ISO 4217 currency unit;
- a schema reference to the profile entry point;
- mapped monetary facts with explicit `contextRef`, `unitRef`, and `decimals`;
- a canonical SHA-256 digest and provenance envelope.

XML is constructed with the Python standard library. The implementation never parses a taxonomy, expands external entities, resolves a DTD, fetches a schema, or executes active content.

## Compatibility and conformance claims

This slice creates a well-formed XBRL 2.1 instance envelope. It does not claim:

- IFRS Accounting Taxonomy conformance;
- DART filing acceptance;
- XBRL Certified Software status;
- Calculations 1.1 validation;
- Formula validation;
- Inline XBRL generation;
- taxonomy-extension authoring.

Those claims require a pinned official taxonomy package, an independent validating processor, jurisdiction fixtures, calculation and formula results, and release evidence.

## Delivery slices

### Implemented in this branch

- canonical artifact construction;
- exact profit-or-loss summary and comparative movement;
- structured explanation records;
- injected taxonomy profile;
- deterministic XBRL 2.1 instance serialization;
- unit tests with statement and branch coverage;
- public package exports;
- ADR, standards traceability, and changelog updates.

### Prepared as successor work

- 3NF taxonomy-profile and report-artifact registries;
- purpose-bound HTTP generation and retrieval;
- object-storage publication with immutable receipts;
- official IFRS Accounting Taxonomy and DART profile packages;
- independent Arelle or certified-processor validation;
- Calculations 1.1 and Formula validation evidence;
- Inline XBRL 1.1 human-readable report generation;
- signed report packages;
- localized management commentary and approval workflow;
- HTML, PDF, spreadsheet, and accessible exact-value renderers.

## Acceptance criteria

- No financial amount is computed with binary floating point.
- Same package, context, and taxonomy profile produce byte-identical artifacts.
- Every emitted fact retains source evidence paths.
- Unknown or inconsistent accounting input fails before XML generation.
- Taxonomy mappings cannot reference missing facts or duplicate a concept-context pair.
- XML contains no external content fetched at runtime.
- Production statement coverage, branch coverage, and public API docstrings remain 100%.
