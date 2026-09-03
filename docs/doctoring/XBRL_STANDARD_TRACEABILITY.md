# XBRL and financial-reporting standards traceability

**Observed:** 2026-09-03  
**Implementation:** `accounting_information_platform.financial_reporting`  
**Decision:** ADR 0067

This document records which external specifications inform the reporting slice, what the repository implements, and what it deliberately does not claim. A specification reference is design input, not evidence of database origin, certification, statutory compliance, filing acceptance, or an audit opinion.

The current implementation is a **proposal formatter and serializer**. It always classifies its caller-supplied source as `proposed`, `caller_supplied_statement_package`, and `unverified`. It cannot create an authoritative Accounting Information Platform report identity.

| External source | Relevant requirement or capability | Product decision | Implementation and evidence | Current status and limitation |
|---|---|---|---|---|
| XBRL 2.1 | XBRL instance root, schema references, entity and period contexts, units, and facts | Emit a deterministic, explicitly unvalidated XBRL 2.1 proposal from a reproducible report proposal | `financial_reporting/xbrl.py`; current/comparison duration and instant context tests; ISO 4217 unit and fact tests | **Proposal envelope implemented.** No AIS source-authority claim, taxonomy loading, schema validation, linkbase processing, formula processing, or filing claim |
| XBRL Open Information Model 1.0 | Common logical model for xBRL-XML, xBRL-JSON, and xBRL-CSV | Keep proposal facts format-neutral and supplied-evidence-linked so later owner-bound OIM projections do not query the ledger independently | `fact_records` in the proposal artifact; stable fact, period, amount, and supplied source fields | **Prepared.** xBRL-JSON and xBRL-CSV serializers are not implemented |
| XBRL Calculations 1.1 | Calculation consistency across facts and period contexts | Keep accounting arithmetic controls in the owner domain, then require an independent Calculations 1.1 result before authoritative statutory publication | exact four-statement and cross-statement controls; successor acceptance criteria in ADR 0067 and Issue #51 | **Not implemented.** Current controls are neither database-origin proof nor a Calculations 1.1 processor/result |
| Inline XBRL 1.1 | Human-readable HTML with embedded XBRL facts | Build Inline XBRL from the same owner-bound artifact after source authority, official profiles, and independent validation exist | renderer handoff and required UI states in `docs/FINANCIAL_REPORTING.md` | **Not implemented.** No HTML/iXBRL claim |
| XBRL Formula 1.0 | Taxonomy-defined business-rule assertions and computations | Treat formula results as independent validation evidence, not as a replacement for accounting-domain controls or source provenance | successor validation registry in ADR 0067 | **Not implemented** |
| Taxonomy Packages 1.0 | Portable taxonomy package identity and catalog handling | Bind every profile to an immutable official-package digest and schema entry point | `XbrlTaxonomyProfile.taxonomy_package_hash`; profile identity tests | **Profile contract implemented.** Package loading, catalog resolution, signature/license verification, and release registry are not implemented |
| Taxonomy Packages 1.1 public working draft | Candidate successor package semantics | Monitor through a later ADR; do not make a public working draft a production contract | ADR 0067 standards baseline | **Monitoring only** |
| Project Tavi public working drafts | Candidate next-generation XBRL report and taxonomy architecture | Preserve a format-neutral proposal and adapters; do not replace XBRL 2.1 before Recommendation and adoption review | canonical proposal facts and injected profile boundary | **Monitoring only** |
| IFRS Accounting Taxonomy 2025 | Current published IFRS digital reporting taxonomy available for 2026 reporting | Do not hard-code or redistribute it. Release a reviewed profile independently using the official package digest and licensed taxonomy content | taxonomy profile port; Issue #51 and ADR 0067 | **No IFRS profile or conformance claim** |
| IFRS Accounting Taxonomy formula linkbase | Validation formulae distributed separately for the IFRS taxonomy | Retain formula processor identity and results as validation evidence when a statutory profile is introduced | planned validation registry | **Not implemented** |
| DART/OpenDART XBRL financial-statement services | Korean filing taxonomy, validation, submission, and data-use context | Treat DART as a jurisdiction adapter and filing authority, not as the accounting ledger or generic taxonomy profile | planned DART profile, validation fixtures, and delivery receipts | **Not implemented. No DART acceptance claim** |
| XML 1.0 and Namespaces in XML | Well-formed XML and namespace-qualified elements | Construct XML with the standard library; validate profile URI, XML prefix, and concept local name before serialization | `contracts.py`, `xbrl.py`, URI/prefix/concept tests | **Implemented for generated proposal syntax.** No external entity or DTD processing exists |
| ISO 4217 representation used by XBRL | Currency unit QName | Require a three-letter uppercase caller-supplied reporting currency and emit `iso4217:{code}` | `FinancialReportContext`; unit tests | **Implemented syntax only.** Currency authority must come from the future AIS owner command |
| FIPS PUB 180-4 SHA-256 | Content identity | Bind supplied statement package, report proposal, taxonomy package, and generated instance to namespaced SHA-256 digests | `primitives.py`, artifact/export tests | **Implemented.** Hash identity is neither a digital signature nor proof of AIS origin |

## Current proposal chain

```text
caller-supplied four-statement-shaped package and report context
→ build_financial_report_artifact
→ proposed / caller_supplied_statement_package / unverified
→ exact fact, arithmetic control, explanation, supplied-source-path, claimed-snapshot, and digest data
→ caller-supplied XbrlTaxonomyProfile
→ export_xbrl_instance
→ unvalidated XBRL 2.1 proposal and digest
→ authoritative_report = false
→ xbrl_validation_status_code = not_run
→ filing_readiness_code = not_ready
```

This chain is deterministic and tamper-evident after construction. It does not prove that the tenant, entity, book, period, currency, dates, account roles, source population, or snapshot references came from AIS-owned PostgreSQL.

## Required authoritative chain

```text
authenticated tenant / actor / purpose / decision
→ AIS owner command
→ PostgreSQL REPEATABLE READ
→ authoritative legal entity / book / fiscal period / currency / date / source population / close state
→ four-statement package and package digest
→ retained report run and report source
→ low-level proposal formatter
→ owner-bound immutable artifact
→ independently released taxonomy profile
→ independent XBRL / Calculations / Formula / jurisdiction validation
→ maker-checker approval
→ authoritative publication and external receipt
```

Only this owner path may issue an authoritative report identity. A live or non-close population must remain provisional or be rejected under explicit policy.

## Authority boundary

- customer usage, pricing, invoice, payment, refund, dispute, and settlement truth belongs to the Billing Control Plane;
- journal, account, period, posting, close, financial-statement, report-source, approval, and publication truth belongs to the Accounting Information Platform;
- taxonomy package publication belongs to the profile publisher;
- XBRL conformance belongs to the identified independent validation result;
- filing acceptance belongs to the regulator or filing authority;
- human-readable copy and localized explanation belong to a renderer/review workflow;
- an LLM-generated narrative is a proposed interpretation, never an accounting fact;
- a content digest, caller claim, test fixture, or taxonomy profile is not source-authority evidence.

No adapter may write a sibling repository database, reuse a mutable sibling PR head as a production dependency, recalculate posted accounting amounts, or elevate a report proposal by accepting a caller-controlled authority flag.

## Validation evidence required before statutory profile release

A statutory profile cannot move from `proposed` to `released` until all of the following evidence is bound to one exact official package and independently reviewed mapping release:

1. official package source and license classification;
2. package SHA-256 and entry-point identity;
3. concept, period, unit, dimension, sign, balance, and disclosure mapping review;
4. current and comparative jurisdiction fixtures;
5. independent XBRL 2.1 schema/linkbase validation;
6. Calculations 1.1 validation;
7. applicable Formula validation;
8. Inline XBRL validation when a human-readable report is produced;
9. regulator-specific validation and sandbox acceptance;
10. source artifact, generated outputs, validator versions, findings, reviewer identities, and approval receipt;
11. statement/branch coverage, docstrings, SAST, security, SBOM, and build provenance at the exact release head;
12. explicit limitations and unsupported disclosures.

A released taxonomy profile is still not sufficient for authoritative report publication; the AIS owner command and report-source/approval evidence are separately required.

## References

IFRS Foundation. (2025). *IFRS Accounting Taxonomy 2025*. https://www.ifrs.org/issued-standards/ifrs-taxonomy/ifrs-accounting-taxonomy-2025/

National Institute of Standards and Technology. (2015). *Secure Hash Standard (SHS)* (FIPS PUB 180-4). https://doi.org/10.6028/NIST.FIPS.180-4

XBRL International. (2003). *Extensible Business Reporting Language (XBRL) 2.1*. https://specifications.xbrl.org/work-product-index-xbrl-2.1.html

XBRL International. (2020). *Taxonomy Packages 1.0*. https://specifications.xbrl.org/work-product-index-taxonomy-packages-1.0.html

XBRL International. (2021). *Open Information Model 1.0*. https://specifications.xbrl.org/work-product-index-open-information-model-1.0.html

XBRL International. (2023). *Calculations 1.1*. https://specifications.xbrl.org/work-product-index-calculations-1.1.html

XBRL International. (2026). *Inline XBRL 1.1*. https://specifications.xbrl.org/work-product-index-inline-xbrl-1.1.html

XBRL International. (2026). *Project Tavi*. https://specifications.xbrl.org/work-product-index-tavi.html
