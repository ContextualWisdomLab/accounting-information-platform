# Financial reporting and XBRL test matrix

**Decision:** ADR 0067  
**Production package:** `accounting_information_platform.financial_reporting`

This matrix separates accounting-domain controls, canonical artifact controls, XML serialization, external taxonomy validation, jurisdiction filing, and user-interface verification. Passing one layer does not imply that another layer passed.

## Implemented automated tests

| Boundary | Required evidence | Current implementation |
|---|---|---|
| Public API | Package root exports the three value objects and two operations | `tests/test_financial_reporting.py` |
| Determinism | Equal source package, context, and profile inputs produce equal artifacts and XBRL bytes | `tests/test_financial_reporting.py` |
| Exact values | Revenue, expense, net income, assets, liabilities, equity, cash, and movement values remain canonical decimal strings | `tests/test_financial_reporting.py` |
| Evidence linkage | Every fact has source statement paths; artifacts retain snapshot references and source hashes | `tests/test_financial_reporting.py` |
| Context contract | Absolute entity scheme, canonical entity identifier, uppercase currency, ordered current/comparison dates, paired comparison dates, bounded integer precision | `tests/test_financial_reporting_context.py` |
| Taxonomy profile | Version, reporting-standard code, release code, XML prefix, namespace, schema reference, package digest, mapping type, and mapping uniqueness | `tests/test_financial_reporting_context.py` |
| Statement identity | Four required statements share tenant, entity, book, period, scope, comparison identity, and statement type | `tests/test_financial_reporting_artifact_validation.py` |
| Line shape | Each line has a canonical role/class/account code, finite non-negative one-sided debit/credit, and exact totals | `tests/test_financial_reporting_artifact_validation.py` |
| Profit or loss | Revenue minus expense reproduces net income | `tests/test_financial_reporting_artifact_validation.py` |
| Financial position | Assets equal liabilities plus equity plus unclosed net income | `tests/test_financial_reporting_artifact_validation.py` |
| Equity rollforward | Opening equity plus period income plus other movement equals closing equity; income and financial-position ties hold | `tests/test_financial_reporting_artifact_validation.py` |
| Cash flow | Operating reconciliation, activity subtotal, opening/closing rollforward, income tie, and authoritative cash-role tie hold | `tests/test_financial_reporting_artifact_validation.py` |
| Comparative completeness | Comparison identity, four statement populations, snapshot evidence, and context dates are all present or all absent | `tests/test_financial_reporting_artifact_validation.py` |
| Structured explanations | Exact parameters, direction, control status, and source evidence are deterministic | `tests/test_financial_reporting.py` and `tests/test_financial_reporting_artifact_validation.py` |
| Artifact integrity | Source hash, artifact hash, context, source package, and every derived field reproduce exactly before export | `tests/test_xbrl_reporting_validation.py` |
| XBRL mapping | Mapped fact exists and profile period type equals the canonical fact period type | `tests/test_xbrl_reporting_validation.py` |
| XBRL contexts and unit | Current/comparison duration and instant contexts and ISO 4217 reporting-currency unit are present as applicable | `tests/test_financial_reporting.py` and `tests/test_xbrl_reporting_validation.py` |
| XBRL fact output | Monetary facts carry context, unit, decimal precision, deterministic XML, and an instance digest | `tests/test_financial_reporting.py` |
| Parser/network absence | No DTD, external entity, schema fetch, taxonomy loader, network client, or model provider exists in the generation path | static source/repository validation and security scans |

## Coverage gate

The repository-wide CI remains authoritative. The focused implementation was also exercised in an isolated harness while the hosted queue was pending. The branch must not be made ready until the exact PR head demonstrates all of the following together:

```text
production statement coverage = 100%
production branch coverage = 100%
public production API docstrings = 100%
full unittest discovery = pass
repository validator = pass
compileall = pass
wheel build and installed public-API smoke = pass
exact-head SAST/security/dependency scans = pass
qualifying independent review = pass
```

A local focused result is development evidence, not merge or release evidence. A queued, pending, cancelled, stale, predecessor, synthetic merge-ref, or skipped applicable check is non-passing.

## Required successor integration tests

### PostgreSQL and report-run persistence

- Generate a report from the supported HTTP/application command and prove that the statement package is read inside one `REPEATABLE READ` transaction.
- Concurrent posting/close and report generation must either serialize or retain the exact source population; no torn package is permitted.
- Same idempotency key plus same command replays one run and artifact; changed context, source, profile, purpose, locale, or target conflicts.
- Runtime tenant RLS prevents reading or publishing another tenant’s run, facts, artifact, profile, validation, approval, or publication receipt.
- Report/run/outbox evidence commits atomically.
- N-1 migration upgrade, backup, point-in-time recovery, restore, and rollback retain artifact/source hashes and publication history.

### Object storage and artifact lifecycle

- Exact content replay does not duplicate an artifact.
- Changed bytes never reuse an artifact identity.
- MIME, byte size, encryption context, KMS key version, object version, hash, retention, legal hold, supersession, and withdrawal are retained.
- A renderer, validator, or LLM cannot write a journal or alter an already-published artifact.
- Missing object, digest mismatch, storage timeout, and partial publication leave an actionable failed state without a false receipt.

### Official taxonomy profile

- Official package source, license classification, package digest, entry point, release, and validator compatibility are verified.
- Canonical fact mapping covers sign, scale, period, balance, unit, dimensions, and disclosures required by the selected filing profile.
- Unsupported facts/disclosures fail with explicit gaps rather than disappearing.
- Taxonomy release change creates a new immutable profile; an old filed artifact remains reproducible.
- Profile withdrawal or supersession cannot rewrite prior validation or publication receipts.

### Independent XBRL validation

- Validate with an independent processor against XBRL 2.1 and the exact package digest.
- Retain Calculations 1.1 and applicable Formula results.
- Verify duplicate facts, inconsistent contexts/units, precision, period boundaries, sign, dimensions, extension concepts, footnotes, and presentation/disclosure rules.
- Processor crash, timeout, unsupported feature, or unavailable dependency is a failed validation, not success.
- Compare at least two processors or an official filing sandbox for critical jurisdiction fixtures before GA.

### DART or another filing authority

- Use only the approved jurisdiction profile and endpoint.
- Authenticate through a purpose-limited filing adapter; no credential enters the report artifact.
- Record submission command, exact artifact, taxonomy profile, validator population, external receipt, acceptance/rejection code, and timestamps.
- Duplicate/retry and out-of-order callback behavior produce one canonical filing state.
- External acceptance is recorded separately from internal report approval and XBRL validation.

### Human-readable rendering

Render the same artifact to accessible HTML, PDF, spreadsheet, and Inline XBRL without changing amount, sign, currency, period, comparison, source reference, or validation state.

Required checks:

- exact-value table parity across formats;
- tagged PDF and reading order;
- spreadsheet formula-injection neutralization and print parity;
- Inline XBRL fact/context/unit parity with the machine-readable instance;
- keyboard-only navigation, visible focus, screen-reader labels, high contrast, and 200% zoom;
- Korean, English, Japanese, Chinese, Vietnamese, Spanish, German, and French text expansion and fallback fonts;
- mobile, intermediate width, print, empty, loading, validation failure, permission denied, superseded, withdrawn, and partial-comparison states;
- no hover-only values or inaccessible chart-only facts.

### Report explanation and LLM interpretation

- Deterministic message-code rendering preserves exact parameters in every locale.
- The Contextual Orchestrator receives only approved evidence and never a credential or unrestricted source database.
- Interpreter output cites fact/evidence identities; an independent verifier rejects unsupported claims, reversed movement direction, correlation-as-causation, missing uncertainty, or accounting-policy invention.
- Human approval is required before publication.
- Model, provider, prompt, reasoning, tool calls, evidence, locale, verifier result, and approval are retained.
- Provider failure, malformed output, missing citation, or disagreement produces `unsupported` or `review_required`, not a plausible narrative.

## Realistic product scenarios

1. Current-year profit rises from the comparative year and the explanation reports the exact increase without inventing its business cause.
2. Net income is unchanged and direction is `unchanged`, including exact zero representation.
3. A loss is represented as an exact signed amount and not relabelled as revenue or expense.
4. A comparison package is unavailable; the report remains usable and explicitly omits movement analysis.
5. A closing entry is posted after an earlier report; a new run produces a new source hash and artifact while the earlier report remains reproducible.
6. An account-role mapping changes prospectively; old reports retain their historical fact and taxonomy mapping evidence.
7. A taxonomy release is superseded; historical XBRL remains reproducible and new reports require the new reviewed profile.
8. A filing authority accepts an artifact that later requires withdrawal; acceptance and withdrawal remain separate append-only facts.
9. A model proposes a cause for profit movement that is absent from evidence; the verifier blocks publication while exact values remain available.
10. An attacker changes a derived fact and recomputes an outer hash; full artifact reproduction rejects the export.

## Non-claims enforced by tests and documentation

Passing the implemented tests means the bounded artifact and serializer contract behaved as specified. It does not mean:

- the report is a complete statutory financial statement;
- an IFRS or DART taxonomy profile is correct;
- the XBRL instance passed an independent processor;
- the report was approved, filed, accepted, audited, or assured;
- Calculations 1.1, Formula, Inline XBRL, or jurisdiction rules passed;
- a UI or localized explanation has been implemented.
