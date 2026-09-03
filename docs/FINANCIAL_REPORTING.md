# Financial reporting, report explanations, and XBRL export

## Scope

The Accounting Information Platform now has two reporting layers with different responsibilities.

```text
PostgreSQL accounting authority
  └─ trial balance and four-statement package
       └─ canonical financial report artifact
            ├─ exact profit-or-loss summary
            ├─ structured report explanations
            ├─ human-readable renderer inputs
            └─ taxonomy-profile-driven XBRL 2.1 instance
```

The first layer already reads posted journals and period-close evidence under one PostgreSQL `REPEATABLE READ` snapshot. It owns the numerical truth. The new `financial_reporting` package does not query journals, infer accounts, alter signs, or change a period. It verifies and packages the existing statement result.

The implemented slice includes:

- current and comparative profit-or-loss headlines;
- exact canonical facts for the income statement, statement of financial position, changes in equity, and cash flow;
- account-role facts for later taxonomy profiles and report renderers;
- source statement paths, snapshot references, source-package hash, and artifact hash;
- machine-readable explanation records;
- a versioned external XBRL taxonomy-profile contract;
- deterministic XBRL 2.1 XML instance generation;
- tamper detection and cross-statement accounting controls.

It does not include a statutory taxonomy package, filing submission, Inline XBRL, formula evaluation, consolidation, notes, segment reporting, or a free-text management commentary generator.

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

report_artifact = build_financial_report_artifact(
    statement_package,
    report_context,
)
```

`statement_package` must be the result of the supported four-statement package boundary or an exact compatible projection. A caller should not create or edit its amounts by hand.

## Profit-or-loss summary

The artifact exposes the current period and, when present, comparative period amounts as canonical decimal strings.

```json
{
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

This is a projection of exact statement lines. It is not a separate profit calculation. The artifact fails when revenue minus expense does not reproduce the income statement’s `net_income_amount`.

## Canonical fact records

Every fact retains the reporting period kind and its source path.

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
- income-statement and financial-position amounts grouped by authoritative `account_role_code`;
- opening, movement, and closing equity facts;
- operating, investing, financing, net-change, opening, and closing cash facts;
- current and optional comparison contexts.

The artifact does not assign statutory taxonomy concepts. That mapping belongs to a reviewed taxonomy profile.

## Accounting controls

Report generation fails closed before any XML or renderer output when one of these conditions is found:

- a required statement is absent;
- statement identity differs from the enclosing package;
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
- the financial-position `cash_receipt` role exists but differs from closing cash.

These are product controls for the current account-role model. They are not a substitute for an XBRL calculation or formula processor.

## Structured report explanations

The artifact generates explanation records, not prose.

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

A web, PDF, spreadsheet, or mobile renderer can translate these codes and insert exact parameters without interpreting raw ledger data. A later LLM explanation workflow may consume the same records, but it must:

- call Contextual Orchestrator rather than a provider directly;
- use only report facts and source evidence paths supplied by the artifact;
- preserve model, provider, prompt, reasoning, evidence, locale, and verifier provenance;
- reject statements not supported by the artifact;
- distinguish arithmetic movement, association, and accounting judgment;
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

The example names are deliberately non-regulatory. Do not copy them into a filing profile. A real profile must be derived from the official package and independently reviewed.

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

## XBRL export

```python
xbrl_export = export_xbrl_instance(
    report_artifact,
    taxonomy_profile,
)
```

The result contains:

```text
export_contract_version
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

1. canonicalizes the supplied artifact;
2. verifies the embedded source-package hash;
3. verifies the artifact hash;
4. reconstructs `FinancialReportContext`;
5. rebuilds the entire report artifact from the embedded source statement package;
6. compares the supplied and rebuilt artifacts exactly;
7. verifies each mapping has a fact and matches its canonical period type.

This prevents a caller from altering a derived fact, recalculating only the outer hash, and obtaining an XBRL document.

The XBRL instance contains:

- a schema reference to the profile entry point;
- one current duration and one current instant context;
- optional comparison duration and instant contexts;
- one ISO 4217 reporting-currency unit;
- mapped monetary facts with `contextRef`, `unitRef`, and `decimals`;
- deterministic XML and SHA-256 identity.

The exporter does not fetch or validate the schema. Network retrieval, DTD parsing, external entity resolution, taxonomy loading, linkbase processing, and active content are absent from this path.

## Conformance and filing status

The implemented output is a deterministic XBRL 2.1 instance envelope. The repository does not yet claim:

- IFRS Accounting Taxonomy conformance;
- DART filing acceptance;
- XBRL Certified Software status;
- Calculations 1.1 validation;
- XBRL Formula validation;
- Inline XBRL generation;
- extension-taxonomy authoring;
- completeness of statutory notes and disclosures.

A production statutory profile requires an official taxonomy-package digest, concept and context mappings, presentation and dimensional decisions, independent processor validation, jurisdiction fixtures, reviewer approval, and immutable release evidence.

## Product Design and renderer handoff

The canonical artifact is the source for the future reporting workspace. Product Design, UX Pilot, Figma, Storybook, and Build Web Apps should begin only after the report-run and publication state machines are fixed.

The minimum screen composition is:

```text
Report period and entity selector
→ four-statement navigation
→ exact-value statement table
→ current/comparison movement view
→ control-status panel
→ evidence drawer
→ explanation review
→ export/package panel
→ validation and publication history
```

Required UI states are:

```text
normal
loading
empty period
comparison unavailable
source snapshot superseded
accounting control failed
taxonomy profile unavailable
XBRL validation failed
permission denied
explanation unsupported
publication pending
published
withdrawn
```

Every chart must have the same exact-value table. HTML, PDF, spreadsheet, and Inline XBRL outputs must preserve values, units, periods, evidence references, and validation status. A button must not be shown unless the matching command exists and its authorization and failure states are implemented.

## Successor implementation sequence

1. Persist taxonomy profiles, mappings, report runs, artifacts, explanations, validation results, approvals, and publication receipts in 3NF tables.
2. Add purpose-bound report generation and retrieval commands to the existing authenticated accounting HTTP boundary.
3. Store immutable artifacts in object storage and retain hash, media type, size, encryption, retention, legal-hold, and supersession evidence.
4. Release official taxonomy profiles independently from the ledger.
5. Integrate an independent XBRL processor and retain XBRL 2.1, Calculations 1.1, Formula, package, and jurisdiction validation results.
6. Implement accessible HTML, PDF, spreadsheet, and Inline XBRL renderers from the canonical artifact.
7. Add localized deterministic explanations and a reviewed Contextual Orchestrator commentary workflow.
8. Add consolidation, foreign currency, statement notes, dimensions, segments, EPS, tax, and disclosure support through separate accounting decisions.
9. Add signed report packages, release provenance, rollback, withdrawal, and regulator/customer delivery receipts.
10. Build and audit the reporting workspace across all supported locales, keyboard use, print, mobile, empty, failure, permission, and supersession states.
