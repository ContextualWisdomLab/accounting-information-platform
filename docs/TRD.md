# Technical Requirements Document

## Architecture style

Begin as a contract-first modular monolith. The accounting domain core is isolated from HTTP, event transport, and persistence adapters. PostgreSQL is the durable authority; the current in-memory ledger is an executable reference oracle for adapter conformance tests.

The `financial_reporting` package is a downstream, stateless reporting boundary. It accepts the existing repeatable-read four-statement package and cannot query journals, post entries, close periods, fetch taxonomies, or invoke a model provider. It is extractable into an independent service only when persistence, rendering queues, filing adapters, or a materially different scaling/security boundary justifies separation.

## Transaction boundary

A durable posting transaction will eventually perform the following atomically:

```text
proposal receipt
+ idempotency and payload-hash decision
+ policy and period resolution
+ general journal
+ journal lines
+ source references
+ posting receipt
+ transactional outbox event
```

No consumer receives a `posted` receipt unless that transaction commits.

Each multithreaded HTTP request uses an independent PostgreSQL transaction.
New sessions set bounded lock and idle-transaction timeouts. State-changing
commands acquire transaction-level advisory locks keyed by tenant and command
scope; posting/reversal re-read the selected period under a shared period lock,
while close selection uses a row lock.
Migration 0006 adds tenant-leading indexes to high-write evidence tables and
records the primary/foreign-key constraints that a future hash-by-tenant/time
partition migration must preserve.

## Precision

- API and event amounts use canonical decimal strings.
- PostgreSQL uses `numeric(38, 6)` in the first milestone.
- Python uses `decimal.Decimal` after strict canonical parsing.
- Binary floating-point types are forbidden in accounting arithmetic.
- Financial-report artifacts and XBRL facts preserve non-exponential canonical decimal text and an explicit decimal precision context.
- Foreign-exchange accounting is explicitly rejected until rate source, rate type, date, rounding, remeasurement, and translation policy are implemented.

## Temporal model

- `transaction_date`: economic transaction date.
- `accounting_date`: requested ledger date.
- `valid_from` and `valid_to`: real-world policy or master-data validity.
- `recorded_at`: system knowledge time.
- `posted_at`: authoritative posting completion.
- `reversed_at`: reversal lineage creation.
- `period_closed_at`: fiscal close control time.
- `current_period_start_date` and `current_period_end_date`: duration and instant context of the current report.
- `comparison_period_start_date` and `comparison_period_end_date`: optional comparative report context; both or neither must exist.

## Contracts

- JSON Schema Draft 2020-12 for payload contracts.
- UUIDv7 for new PostgreSQL record identifiers.
- SHA-256 source hashes for immutable evidence identity.
- Idempotency keys for every state-changing external command.
- CloudEvents-compatible outbox events in the service milestone.
- XBRL 2.1 for the first XML instance envelope.
- XBRL Open Information Model 1.0 as the future common logical basis for xBRL-JSON and xBRL-CSV projections.
- XBRL Calculations 1.1 and Formula validation are independent successor validation gates, not hidden inside ledger arithmetic.
- Inline XBRL 1.1 is the future human-readable XBRL format; this slice does not generate it.

## Canonical financial-report artifact

`build_financial_report_artifact` SHALL:

1. accept only a mapping-compatible four-statement package and a validated `FinancialReportContext`;
2. canonicalize the source package as sorted UTF-8 JSON with no NaN or infinity;
3. verify tenant, legal entity, book, fiscal period, statement scope, comparison identity, and statement type across all four statements;
4. verify each line, debit/credit total, profit-or-loss result, financial-position equation, changes-in-equity rollforward, cash-flow rollforward, and cross-statement ties;
5. emit exact current and optional comparison facts with canonical fact code, exact amount text, statement type, period type, period context, and source evidence paths;
6. emit deterministic explanation records with code, status, direction, exact parameters, and source paths;
7. retain source snapshot references and the complete canonical source statement package;
8. bind the source package and report artifact to namespaced SHA-256 identities;
9. contain no current clock, random identifier, mutable external label, or provider call.

Equal package and context inputs SHALL produce equal artifacts.

## XBRL taxonomy profile

`XbrlTaxonomyProfile` SHALL be an immutable value object containing:

- profile identifier and positive profile version;
- reporting-standard and taxonomy-release codes;
- non-reserved XML prefix;
- absolute taxonomy namespace URI and schema entry-point URI;
- lowercase `sha256:` identity of the official or reviewed taxonomy package;
- one-to-one `XbrlConceptMapping` values from canonical fact code to taxonomy concept local name and `duration` or `instant` period type.

The ledger SHALL NOT contain hard-coded IFRS, DART, or another jurisdiction taxonomy. Official profiles are independently reviewed release artifacts. Provider or regulator IDs are not internal primary keys.

## XBRL export

`export_xbrl_instance` SHALL:

1. canonicalize and validate its report-artifact input;
2. verify the source-package and report-artifact hashes;
3. reconstruct the report context;
4. rebuild the complete report artifact from its embedded source package and reject any difference;
5. reject an absent mapped fact or a mapping whose period type differs from the canonical fact;
6. emit XBRL 2.1 entity, duration/instant context, ISO 4217 unit, schema-reference, and monetary fact elements;
7. return deterministic XML, media type, file name, report and taxonomy provenance, and XML SHA-256 identity;
8. perform no network retrieval, DTD parsing, external-entity resolution, taxonomy loading, linkbase processing, or active-content execution.

The output is a generated XBRL 2.1 instance envelope. It is not evidence of IFRS conformance, DART acceptance, Calculations 1.1 validation, Formula validation, Inline XBRL validity, XBRL certification, or audit assurance.

## Report explanation

The first explanation contract SHALL be deterministic and language-neutral. It SHALL expose machine-readable message codes and exact parameters for current profit or loss, comparative net-income movement, financial-position balance, equity rollforward, and cash rollforward.

A future localized renderer SHALL consume the versioned translation resource rather than shipping a private catalog. A future LLM interpreter SHALL call Contextual Orchestrator and SHALL receive only the canonical evidence bundle. Every narrative claim requires fact/evidence references, model/prompt provenance, an independent verifier result, and human approval before external publication. A model output SHALL NOT change report facts or control results.

## Persistence successor

A later migration SHALL normalize, at minimum:

```text
financial_report_run
financial_report_artifact
financial_report_fact
financial_report_explanation
taxonomy_profile
taxonomy_concept_mapping
report_validation_run
report_validation_result
report_approval_record
report_publication_receipt
```

The command that creates a report SHALL load the existing four-statement package under the accounting authority boundary, use tenant and purpose authorization, bind an idempotency key and immutable source evidence, publish large artifacts to controlled object storage, and commit its report/run/outbox evidence atomically. No renderer or validator may write journal truth.

## Security

- Tenant scope is carried on every authoritative record.
- Composite foreign keys prevent cross-tenant relation construction.
- PostgreSQL row-level security uses an explicit session tenant context.
- Source payload bodies remain outside journal tables; only immutable references and hashes are stored.
- Report artifacts may contain complete statement evidence and must be classified, encrypted, retained, legally held, exported, superseded, and withdrawn according to purpose-specific policy.
- Taxonomy and validator packages are untrusted inputs until digest, license, signature/provenance, parser bounds, and independent review pass.
- XBRL generation does not fetch remote schema references or process external entities.
- Prompt, response, token, credential, and provider secrets never enter canonical report facts.
