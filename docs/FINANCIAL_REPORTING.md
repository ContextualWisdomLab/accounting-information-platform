# Financial reporting, report explanations, and XBRL export

## Scope and authority status

The Accounting Information Platform now has a low-level reporting proposal layer after the existing accounting-authority layer.

```text
AIS-owned PostgreSQL accounting authority
  └─ trial balance and four-statement package
       └─ future owner-controlled report command
            ├─ authoritative source/currency/date/snapshot provenance
            ├─ validation and maker-checker approval
            └─ authoritative publication receipt

Caller-supplied package and report context
  └─ financial_reporting pure functions
       └─ proposed / caller_supplied_statement_package / unverified
            ├─ exact profit-or-loss summary
            ├─ structured report explanations
            ├─ renderer proposal input
            └─ unvalidated XBRL 2.1 proposal
```

The existing statement package reads posted journals and period-close evidence under one PostgreSQL `REPEATABLE READ` snapshot. That owner path is the numerical authority. The new pure functions do not query PostgreSQL and therefore **cannot prove that their input came from that owner path**. They verify arithmetic and serialize a caller-supplied package, but always return a non-authoritative proposal.

Every artifact created by `build_financial_report_artifact` has:

```text
truth_status_code = proposed
source_authority_code = caller_supplied_statement_package
publication_readiness_code = unverified
authoritative_report = false
report_artifact_reference = urn:cwl:accounting:financial_report_proposal:{sha256}
```

Every `export_xbrl_instance` result preserves that classification and also has:

```text
xbrl_validation_status_code = not_run
filing_readiness_code = not_ready
authoritative_report = false
```

A content hash proves byte identity, not AIS origin. Caller-supplied entity, currency, dates, snapshot IDs, report context, or a taxonomy profile cannot elevate the proposal.

## Implemented proposal slice

The implemented slice includes:

- current and comparative profit-or-loss headlines;
- exact canonical facts for the income statement, statement of financial position, changes in equity, and cash flow;
- account-role facts for later taxonomy profiles and report renderers;
- claimed source statement paths and snapshot references;
- source-package and proposal-artifact hashes;
- machine-readable explanation records;
- a versioned external XBRL taxonomy-profile contract;
- deterministic XBRL 2.1 XML proposal generation;
- tamper detection and cross-statement arithmetic controls;
- explicit truth, validation, filing, and authority status.

It does not include a PostgreSQL-owned report command, statutory taxonomy package, independent XBRL validation, filing submission, Inline XBRL, formula evaluation, consolidation, notes, segment reporting, or free-text management commentary.

## Public API

```python
from datetime import date

from accounting_information_platform import (
    FinancialReportContext,
    XbrlConceptMapping,
    XbrlTaxonomyProfile,
    build_financial_report_artifact,
    export_xbrl_instance,
)

report_context = FinancialReportContext(
    entity_identifier_scheme="https://registry.example/entities",
    entity_identifier_value="ENTITY-001",
    reporting_currency_code="KRW",
    current_period_start_date=date(2026, 1, 1),
    current_period_end_date=date(2026, 12, 31),
    comparison_period_start_date=date(2025, 1, 1),
    comparison_period_end_date=date(2025, 12, 31),
    decimal_precision=0,
)

report_proposal = build_financial_report_artifact(
    statement_package,
    report_context,
)
```

`statement_package` and `report_context` are caller-supplied at this layer. A caller should use the supported four-statement package, but the pure function cannot attest that it did. The result is consequently suitable for controlled preparation, testing, format development, and later owner-bound processing—not for authoritative publication by itself.

## Profit-or-loss summary

The proposal exposes current and, when present, comparative amounts as canonical decimal strings.

```json
{
  "truth_status_code": "proposed",
  "publication_readiness_code": "unverified",
  "profit_and_loss_summary": {
    "revenue_amount": "1200.50",
    "expense_amount": "200.25",
    "net_income_amount": "1000.25",
    "comparison_revenue_amount": "1000.00",
    "comparison_expense_amount": "200.00",
    "comparison_net_income_amount": "800.00",
    "net_income_change_amount": "200.25",
    "net_income_direction_code": "increase"
  }
}
```

This is a projection of exact statement lines. It is not a separate profit calculation. The builder fails when revenue minus expense does not reproduce the supplied income statement’s `net_income_amount`.

## Canonical fact records

Every fact retains its reporting period kind and supplied source path.

```json
{
  "fact_code": "profit_loss.revenue_amount",
  "fact_amount": "1200.50",
  "period_context_code": "current",
  "statement_type_code": "income_statement",
  "period_type_code": "duration",
  "source_evidence_paths": [
    "income_statement.statement_lines[0]"
  ]
}
```

The first implementation produces:

- profit-or-loss revenue, expense, and net income;
- financial-position assets, liabilities, equity, and unclosed net income;
- income-statement and financial-position amounts grouped by supplied `account_role_code`;
- opening, movement, and closing equity facts;
- operating, investing, financing, net-change, opening, and closing cash facts;
- current and optional comparison contexts.

The proposal does not assign statutory taxonomy concepts. That mapping belongs to a separately reviewed taxonomy profile. The account-role code is treated as part of the supplied package until the future owner command binds it to AIS-owned account-role mapping and statement provenance.

## Arithmetic controls

Proposal generation fails closed before XML or renderer output when one of these conditions is found:

- a required statement is absent;
- statement identity differs within the supplied package;
- comparison identity, statement data, or report dates are only partially supplied;
- a monetary value is malformed, non-finite, negative where prohibited, or present on both debit and credit sides;
- a statement’s line totals differ from its reported totals;
- revenue minus expense differs from net income;
- assets differ from liabilities plus equity plus unclosed net income;
- opening equity plus current-period income plus other equity movement differs from closing equity;
- equity-period income differs from income-statement net income;
- closing equity differs from financial-position equity plus unclosed net income;
- cash from operations differs from net income plus working-capital adjustment;
- operating plus investing plus financing cash differs from net cash change;
- opening cash plus net cash change differs from closing cash;
- cash-flow period income differs from income-statement net income;
- the supplied financial-position `cash_receipt` role exists but differs from closing cash.

These controls prove internal consistency of the supplied package. They do not prove database origin, statutory completeness, XBRL calculation validity, or audit assurance.

## Structured report explanations

The proposal generates explanation records, not prose.

```json
{
  "explanation_code": "profit_loss.net_income_change",
  "status_code": "informational",
  "direction_code": "increase",
  "parameter_map": {
    "current_net_income_amount": "1000.25",
    "comparison_net_income_amount": "800.00",
    "change_amount": "200.25"
  },
  "source_evidence_paths": [
    "income_statement.net_income_amount",
    "income_statement.comparison_net_income_amount"
  ]
}
```

Initial explanation codes are:

```text
profit_loss.current_summary
profit_loss.net_income_change
financial_position.equation
changes_in_equity.rollforward
cash_flow.rollforward
```

The records explain arithmetic composition, movement direction, and control state only. They do not infer why revenue, expense, profit, equity, or cash changed.

A later renderer can resolve the codes through the versioned translation resource. A later LLM workflow must:

- call Contextual Orchestrator rather than a provider directly;
- receive an owner-bound report/evidence bundle, not unrestricted accounting tables;
- preserve model, provider, prompt, reasoning, tool, evidence, locale, and verifier provenance;
- reject unsupported business causes, reversed movement direction, invented policy, correlation-as-causation, and missing uncertainty;
- require human approval before external publication;
- never replace the exact-value report or control status.

## Taxonomy-profile contract

A taxonomy profile is an external, immutable mapping release.

```python
taxonomy_profile = XbrlTaxonomyProfile(
    profile_identifier="reviewed-profile-identifier",
    profile_version=1,
    reporting_standard_code="reviewed_standard_code",
    taxonomy_release_code="reviewed_release_code",
    taxonomy_prefix="reviewedPrefix",
    taxonomy_namespace_uri="https://taxonomy.example/namespace",
    schema_reference_uri="https://taxonomy.example/entry-point.xsd",
    taxonomy_package_hash="sha256:...",
    concept_mappings=(
        XbrlConceptMapping(
            fact_code="profit_loss.net_income_amount",
            concept_local_name="ReviewedProfitLossConcept",
            period_type_code="duration",
        ),
    ),
)
```

The example names are deliberately non-regulatory. Do not copy them into a filing profile. A real profile must be derived from an official package, reviewed independently, released immutably, and bound to license and provenance evidence.

The profile rejects:

- a missing or non-positive version;
- a noncanonical reporting-standard code;
- reserved or invalid XML prefixes;
- relative namespace or schema-reference URIs;
- a package identity that is not a lowercase SHA-256 digest;
- an empty mapping set;
- repeated canonical fact codes;
- repeated taxonomy concept names;
- invalid concept local names;
- period types other than `duration` and `instant`.

A valid taxonomy profile still does not prove the report source is authoritative or that an instance conforms to the taxonomy.

## XBRL proposal export

```python
xbrl_proposal = export_xbrl_instance(
    report_proposal,
    taxonomy_profile,
)
```

The result contains:

```text
export_contract_version
truth_status_code = proposed
source_authority_code = caller_supplied_statement_package
publication_readiness_code = unverified
authoritative_report = false
xbrl_validation_status_code = not_run
filing_readiness_code = not_ready
media_type = application/xbrl+xml
file_name
report_artifact_reference
report_artifact_hash
taxonomy_profile_identifier
taxonomy_profile_version
reporting_standard_code
taxonomy_release_code
taxonomy_package_hash
xbrl_instance_hash
xbrl_instance
```

Before serializing, the exporter:

1. canonicalizes the supplied proposal;
2. verifies the embedded source-package hash;
3. verifies the proposal-artifact hash;
4. reconstructs `FinancialReportContext`;
5. rebuilds the complete proposal from the embedded source statement package;
6. compares the supplied and rebuilt proposals exactly;
7. verifies each mapping has a fact and matches its canonical period type.

This prevents a caller from altering a derived fact, recalculating only the outer hash, and obtaining an XBRL output. It does not prove where the original package or context came from.

The XML contains:

- a schema reference to the profile entry point;
- one current duration and one current instant context;
- optional comparison duration and instant contexts;
- one ISO 4217 reporting-currency unit;
- mapped monetary facts with `contextRef`, `unitRef`, and `decimals`;
- deterministic bytes and SHA-256 identity.

The exporter does not fetch or validate the schema. Network retrieval, DTD parsing, external entity resolution, taxonomy loading, linkbase processing, and active content are absent from this path.

## Authoritative report path still required

The future high-level command must accept identifiers and purpose context rather than report numbers. It must obtain and retain:

- authenticated tenant, actor, purpose, and decision context;
- AIS-owned legal entity, accounting book, fiscal period, chart-account/reporting policy, and reporting currency;
- current and comparison dates from the fiscal calendar;
- the four statements from one PostgreSQL `REPEATABLE READ` transaction;
- source journal or hard-close snapshot population;
- close/live/provisional state and knowledge cutoff;
- exact package, proposal, taxonomy-profile, validator, approval, and output digests;
- append-only report-run, validation, maker-checker approval, publication, rejection, supersession, and withdrawal evidence.

Only that owner path may issue an authoritative report identity, and only after the policy-required validation and approval gates. A live/non-close population must remain provisional or be rejected for publication.

## Conformance and filing status

The implemented output is an unverified XBRL 2.1 instance proposal. The repository does not claim:

- AIS database origin from the pure builder;
- IFRS Accounting Taxonomy conformance;
- DART filing acceptance;
- XBRL Certified Software status;
- Calculations 1.1 validation;
- XBRL Formula validation;
- Inline XBRL generation;
- extension-taxonomy authoring;
- completeness of statutory notes and disclosures;
- approval, publication, filing, audit, or assurance.

A production statutory profile requires an official taxonomy-package digest, concept and context mappings, presentation and dimensional decisions, independent processor validation, jurisdiction fixtures, owner-bound source provenance, reviewer approval, and immutable release evidence.

## Product Design and renderer handoff

The **owner-bound authoritative artifact**, not an arbitrary proposal, is the eventual source for the reporting workspace. Product Design, UX Pilot, Figma, Storybook, and Build Web Apps should begin only after report-run and publication state machines are fixed.

Minimum composition:

```text
Report period and entity selector
→ four-statement navigation
→ exact-value statement table
→ current/comparison movement view
→ authority and control-status panel
→ evidence drawer
→ explanation review
→ taxonomy/validation panel
→ export/package panel
→ approval and publication history
```

Required UI states:

```text
normal
loading
empty_period
comparison_unavailable
unverified_proposal
provisional_live_population
source_snapshot_superseded
accounting_control_failed
taxonomy_profile_unavailable
xbrl_validation_failed
permission_denied
explanation_unsupported
publication_pending
published
rejected
withdrawn
```

Every chart must have the same exact-value table. HTML, PDF, spreadsheet, and Inline XBRL outputs must preserve values, units, periods, evidence references, truth status, validation status, and publication status. A button must not appear unless the matching command, authorization, loading, failure, cancellation, and recovery behavior exists.

## Successor implementation sequence

1. Implement the PostgreSQL owner report command and source-authority receipt.
2. Persist taxonomy profiles, mappings, report runs, sources, artifacts, explanations, validation results, approvals, publication receipts, supersession, and withdrawal in 3NF tables.
3. Store immutable artifacts in object storage with content verification, tenant encryption, retention, legal hold, and non-overwrite publication.
4. Release official taxonomy profiles independently from the ledger.
5. Integrate an independent XBRL processor and retain XBRL 2.1, Calculations 1.1, Formula, package, and jurisdiction results.
6. Implement accessible HTML, PDF, spreadsheet, and Inline XBRL renderers from the owner-bound artifact.
7. Add localized deterministic explanations and a reviewed Contextual Orchestrator commentary workflow.
8. Add consolidation, foreign currency, statement notes, dimensions, segments, EPS, tax, and disclosure support through separate accounting decisions.
9. Add signed report packages, release provenance, rollback, withdrawal, and regulator/customer delivery receipts.
10. Build and audit the reporting workspace across all supported locales, keyboard use, print, mobile, empty, failure, permission, provisional, superseded, and withdrawal states.
