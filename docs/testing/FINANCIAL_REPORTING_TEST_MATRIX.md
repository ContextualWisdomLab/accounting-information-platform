# Financial reporting and XBRL test matrix

**Decision:** ADR 0067  
**Production package:** `accounting_information_platform.financial_reporting`

This matrix separates supplied-package arithmetic, proposal classification, AIS source authority, XML serialization, external taxonomy validation, jurisdiction filing, and user-interface verification. Passing one layer does not imply that another layer passed.

## Implemented automated tests

| Boundary | Required evidence | Current implementation |
|---|---|---|
| Public API | Package root exports the three value objects and two operations | `tests/test_financial_reporting.py` |
| Determinism | Equal supplied package, context, and profile inputs produce equal proposal artifacts and XBRL bytes | `tests/test_financial_reporting.py` |
| Authority classification | Every low-level artifact is `proposed`, `caller_supplied_statement_package`, `unverified`, non-authoritative, and uses the `financial_report_proposal` URN namespace | `tests/test_financial_reporting.py` |
| Arbitrary balanced input | An unrecorded tenant/entity with relabelled caller currency and dates never receives authoritative report or filing-ready status | `tests/test_financial_reporting.py` |
| Exact values | Revenue, expense, net income, assets, liabilities, equity, cash, and movement values remain canonical decimal strings | `tests/test_financial_reporting.py` |
| Supplied evidence linkage | Every fact has supplied statement paths; proposals retain claimed snapshot references and source hashes without treating them as database authority | `tests/test_financial_reporting.py` |
| Context contract | Absolute entity scheme, canonical entity identifier, uppercase currency, ordered current/comparison dates, paired comparison dates, bounded integer precision | `tests/test_financial_reporting_context.py` |
| Taxonomy profile | Version, reporting-standard code, release code, XML prefix, namespace, schema reference, package digest, mapping type, and mapping uniqueness | `tests/test_financial_reporting_context.py` |
| Statement identity | Four required supplied statements share tenant, entity, book, period, scope, comparison identity, and statement type | `tests/test_financial_reporting_artifact_validation.py` |
| Line shape | Each supplied line has a canonical role/class/account code, finite non-negative one-sided debit/credit, and exact totals | `tests/test_financial_reporting_artifact_validation.py` |
| Profit or loss | Revenue minus expense reproduces supplied net income | `tests/test_financial_reporting_artifact_validation.py` |
| Financial position | Supplied assets equal liabilities plus equity plus unclosed net income | `tests/test_financial_reporting_artifact_validation.py` |
| Equity rollforward | Supplied opening equity plus period income plus other movement equals closing equity; income and financial-position ties hold | `tests/test_financial_reporting_artifact_validation.py` |
| Cash flow | Supplied operating reconciliation, activity subtotal, opening/closing rollforward, income tie, and claimed cash-role tie hold | `tests/test_financial_reporting_artifact_validation.py` |
| Comparative completeness | Comparison identity, four statement populations, claimed snapshot evidence, and context dates are all present or all absent | `tests/test_financial_reporting_artifact_validation.py` |
| Structured explanations | Exact parameters, direction, control status, and supplied source paths are deterministic; no unobserved business cause is generated | `tests/test_financial_reporting.py` and `tests/test_financial_reporting_artifact_validation.py` |
| Artifact integrity | Source hash, proposal hash, context, supplied source package, and every derived field reproduce exactly before export | `tests/test_xbrl_reporting_validation.py` |
| XBRL mapping | Mapped fact exists and profile period type equals the canonical proposal fact period type | `tests/test_xbrl_reporting_validation.py` |
| XBRL contexts and unit | Current/comparison duration and instant contexts and ISO 4217 reporting-currency unit are present as applicable | `tests/test_financial_reporting.py` and `tests/test_xbrl_reporting_validation.py` |
| XBRL proposal status | Export preserves `proposed`, `unverified`, `authoritative_report=false`, `validation=not_run`, and `filing=not_ready` | `tests/test_financial_reporting.py` |
| XBRL fact output | Monetary facts carry context, unit, decimal precision, deterministic XML, and an instance digest | `tests/test_financial_reporting.py` |
| Parser/network absence | No DTD, external entity, schema fetch, taxonomy loader, network client, or model provider exists in the generation path | static source/repository validation and security scans |

## Not implemented: AIS source-authority proof

The current pure functions cannot prove that a package, account role, entity, book, period, currency, date, journal population, close state, or snapshot reference came from AIS-owned PostgreSQL. They are intentionally unable to issue an authoritative report identity.

The successor owner command needs separate failing and passing tests proving:

- authenticated tenant/actor/purpose/decision selection;
- database-controlled tenant isolation;
- owner-derived reporting currency and fiscal dates;
- one PostgreSQL `REPEATABLE READ` statement/source population;
- retained journal or hard-close snapshot population and knowledge cutoff;
- explicit provisional/rejected treatment of live/non-close populations;
- atomic report-run, source, proposal, artifact, and outbox evidence;
- validation and maker-checker approval before authoritative publication.

A caller flag, hash, database-looking ID, in-memory ledger, or test fixture cannot satisfy these tests.

## Coverage gate

The repository-wide CI remains authoritative. Focused local/isolated execution is development evidence only. The branch must not be made ready until the exact PR head demonstrates all of the following together:

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

A queued, pending, cancelled, stale, predecessor, synthetic merge-ref, or skipped applicable check is non-passing. A focused test result from an earlier head is not evidence for a later authority or documentation fix.

## Required successor integration tests

### PostgreSQL and report-run persistence

- Generate a report from the supported owner command and prove that the statement package is read inside one `REPEATABLE READ` transaction.
- Concurrent posting/close and report generation must either serialize or retain the exact source population; no torn package is permitted.
- Same idempotency key plus same command replays one run and artifact; changed entity, book, period, source, context, profile, purpose, locale, or target conflicts.
- Runtime tenant RLS prevents reading or publishing another tenant’s run, facts, artifact, profile, validation, approval, or publication receipt.
- Report/run/source/artifact/outbox evidence commits atomically.
- Currency and dates differ from caller suggestions whenever authoritative legal-entity/book/calendar policy says otherwise.
- A live/non-close source population cannot be represented as final without an explicit approved policy.
- N-1 migration upgrade, backup, point-in-time recovery, restore, and rollback retain artifact/source hashes and publication history.

### Object storage and artifact lifecycle

- Exact content replay does not duplicate an artifact.
- Changed bytes never reuse an artifact identity.
- MIME, byte size, encryption context, KMS key version, object version, hash, retention, legal hold, supersession, and withdrawal are retained.
- A renderer, validator, filing adapter, or LLM cannot write a journal or alter an already-published artifact.
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
- Record submission command, exact owner-bound artifact, taxonomy profile, validator population, external receipt, acceptance/rejection code, and timestamps.
- Duplicate/retry and out-of-order callback behavior produce one canonical filing state.
- External acceptance is recorded separately from internal source authority, report approval, and XBRL validation.

### Human-readable rendering

Render the same owner-bound artifact to accessible HTML, PDF, spreadsheet, and Inline XBRL without changing amount, sign, currency, period, comparison, source reference, truth status, or validation state.

Required checks:

- exact-value table parity across formats;
- tagged PDF and reading order;
- spreadsheet formula-injection neutralization and print parity;
- Inline XBRL fact/context/unit parity with the machine-readable instance;
- keyboard-only navigation, visible focus, screen-reader labels, high contrast, and 200% zoom;
- Korean, English, Japanese, Chinese, Vietnamese, Spanish, German, and French text expansion and fallback fonts;
- mobile, intermediate width, print, empty, loading, unverified, provisional, validation failure, permission denied, superseded, withdrawn, and partial-comparison states;
- no hover-only values or inaccessible chart-only facts.

### Report explanation and LLM interpretation

- Deterministic message-code rendering preserves exact parameters in every locale.
- The Contextual Orchestrator receives only approved owner-bound evidence and never a credential or unrestricted source database.
- Interpreter output cites fact/evidence identities; an independent verifier rejects unsupported causes, reversed movement direction, correlation-as-causation, missing uncertainty, or accounting-policy invention.
- Human approval is required before publication.
- Model, provider, prompt, reasoning, tool calls, evidence, locale, verifier result, and approval are retained.
- Provider failure, malformed output, missing citation, or disagreement produces `unsupported` or `review_required`, not a plausible narrative.

## Realistic product scenarios

1. Current-year profit rises from the comparative year and the explanation reports the exact increase without inventing its business cause.
2. Net income is unchanged and direction is `unchanged`, including exact zero representation.
3. A loss is represented as an exact signed amount and not relabelled as revenue or expense.
4. A comparison package is unavailable; the proposal remains usable and explicitly omits movement analysis.
5. An attacker supplies a perfectly balanced package for an unrecorded tenant/entity and relabels it to USD/2030; output remains proposed, unverified, and non-authoritative.
6. A closing entry is posted after an earlier owner-bound report; a new run produces a new source hash and artifact while the earlier report remains reproducible.
7. An account-role mapping changes prospectively; old reports retain their historical fact and taxonomy mapping evidence.
8. A taxonomy release is superseded; historical XBRL remains reproducible and new reports require the new reviewed profile.
9. A filing authority accepts an artifact that later requires withdrawal; acceptance and withdrawal remain separate append-only facts.
10. A model proposes a cause for profit movement that is absent from evidence; the verifier blocks publication while exact values remain available.
11. An attacker changes a derived fact and recomputes an outer hash; full proposal reproduction rejects the export.

## Non-claims enforced by tests and documentation

Passing the implemented tests means the bounded proposal and serializer contract behaved as specified. It does not mean:

- the proposal came from AIS-owned PostgreSQL;
- the report is a complete statutory financial statement;
- an IFRS or DART taxonomy profile is correct;
- the XBRL instance passed an independent processor;
- the report was validated, approved, published, filed, accepted, audited, or assured;
- Calculations 1.1, Formula, Inline XBRL, or jurisdiction rules passed;
- a UI or localized explanation has been implemented.
