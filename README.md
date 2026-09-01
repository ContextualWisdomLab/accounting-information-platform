# Accounting Information Platform

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/ContextualWisdomLab/accounting-information-platform)

**Evidence-backed accounting posting and financial reporting control plane.**

Accounting Information Platform is ContextualWisdomLab's statutory accounting boundary. It accepts evidence-backed journal proposals from approved source systems, resolves legal entity, accounting book, fiscal period, currency, chart-account roles, and policy, and returns an authoritative accounting outcome. A balanced proposal is not a posted journal.

The product is independently deployable and does not require Naruon, Metering Billing Platform, or another sibling repository to be cloned or running. Other products integrate through explicit contracts rather than shared application tables.

## Why it exists

Billing, payment, payroll, commerce, and operational systems describe economic events. They should not silently decide the final book treatment. Accounting Information Platform creates a dedicated control boundary for deciding what is posted, under which policy and period, with replay-safe evidence and preserved audit lineage.

| Need | What the platform owns |
| --- | --- |
| Journal authority | Validate and post evidence-backed journal proposals or fail closed |
| Policy resolution | Resolve effective accounting policy, chart roles, currency and fiscal period |
| Replay safety | Distinguish exact idempotent replay from conflicting reuse |
| Immutable correction | Reverse through equal-and-opposite accounting entries rather than destructive edits |
| Reconciliation | Persist book-to-bank reconciliation runs, proposals, exceptions and evidence |
| Financial reads | Trial balance, ledger and supported financial-reporting projections |
| Bank evidence | Preserve normalized bank-statement evidence with source provenance |
| Auditability | Preserve posting, reversal, reconciliation and source-evidence lineage without conflating source and book authority |

## Current maturity

The current source tree is package version `0.1.0` and is explicitly **Pre-Alpha**. It contains an executable reference core, normalized PostgreSQL persistence, closed JSON contracts, a mountable HTTP surface, reconciliation foundations, and product/security/operability documentation.

It is **not** a turnkey hosted accounting service and does not automatically start an HTTP listener. Current source also does not claim complete jurisdiction-specific compliance, live tax-authority submission, consolidation, foreign-exchange accounting, revenue schedules, production certification, or complete purpose-bound lifecycle authorization. See the [product and technical gap baseline](docs/product-technical-gap-baseline.md) for evidence-bound readiness and remaining work.

## Authority boundary

```text
Source systems
  │ economic / commercial facts
  ▼
Metering Billing Platform or other approved producer
  │ versioned accounting_journal_proposal
  ▼
┌──────────────────────────────────┐
│ Accounting Information Platform  │
├──────────────────────────────────┤
│ policy + period resolution       │
│ chart-account role mapping       │
│ posting / fail-closed admission  │
│ reversal and reconciliation      │
│ ledger / reporting evidence      │
└───────────────┬──────────────────┘
                │ accounting_posting_receipt
                ▼
        consuming applications
```

Source systems retain authority for the business events they produce. This repository owns legal books, posted journals, fiscal-period control, final chart-account resolution, accounting reversals, reconciliation records, trial balances, and its accounting policy/receipt contracts.

A source system may propose semantic roles such as `accounts_receivable` or `usage_revenue`; it cannot bypass accounting policy by choosing final chart-account identifiers or writing journal tables directly.

## Install and verify

Requires Python 3.13 or newer. The reference core has no runtime package dependencies.

```bash
python3 -m pip install --no-deps --no-build-isolation -e .
PYTHONPATH=src:. python3 -m unittest discover -s tests -p 'test_*.py' -v
```

For the repository's full reproducible quality contract, including hash-locked tooling, 100% coverage gates, PostgreSQL-backed tests and repository validation, follow [`docs/TEST_STRATEGY.md`](docs/TEST_STRATEGY.md) and the current CI workflow rather than copying an abbreviated command list from this README.

Some persistence tests require a reachable PostgreSQL 18 instance and `ACCOUNTING_DATABASE_URL`; the test strategy documents that boundary explicitly.

## Integrate a producer or consumer

Integration uses versioned contracts under [`schemas/`](schemas/) and the documented HTTP/in-process boundary. No sibling should write accounting tables or depend on an undeclared payload.

The three primary contract responsibilities are:

| Contract | Authority |
| --- | --- |
| Journal proposal | Approved source / Metering Billing Platform |
| Accounting policy manifest | Accounting Information Platform |
| Posting receipt | Accounting Information Platform |

The essential flow is:

1. An approved producer submits a balanced proposal with tenant/legal-entity identity, evidence hash, semantic account roles and an idempotency key.
2. Accounting resolves policy and chart accounts under the effective book/period boundary.
3. The current reference core either posts a valid proposal and returns its receipt or fails closed with a validation/idempotency error. `held` and `rejected` are reserved receipt-contract outcomes for the broader service milestone; this README does not claim they are emitted by the current admission path.
4. Exact replay returns the existing outcome; conflicting reuse fails closed.
5. Corrections preserve the original accounting evidence and use an explicit reversal path.

See [`docs/ACCOUNTING_BOUNDARY.md`](docs/ACCOUNTING_BOUNDARY.md) and [`docs/TRD.md`](docs/TRD.md) for the full contract and transport requirements.

## Reconciliation

The integrated foundation includes an **immutable camt.053.001.14 bank-statement evidence registry**, a **deterministic reconciliation proposal engine**, an **exact book-to-bank bridge**, and **durable reconciliation runs, exceptions, and evidence**. These capabilities preserve independent bank evidence and review lineage rather than treating matching as an irreversible side effect.

Reconciliation completion hardening is still stacked beyond the protected foundation. Full cross-run many-to-many allocation, complete purpose-bound lifecycle authorization, close-package provenance, and buyer-facing workflow completeness remain explicit gaps where the durable baseline says they are not yet complete.

Implementation-specific migration and table identities stay in the [data model](docs/DATA_MODEL.md), migration chain, and product-gap baseline rather than customer-facing copy.

## Architecture at a glance

```text
Contracts / source evidence
          │
          ▼
┌───────────────────────────────┐
│ Accounting application layer │
│ validation + policy control   │
├───────────────────────────────┤
│ Posting aggregate            │
│ Reversal boundary            │
│ Reconciliation boundary      │
│ Reporting projections        │
└──────────────┬────────────────┘
               │
        PostgreSQL authority
               │
        audit / outbox evidence
```

The in-memory reference model is a correctness oracle; durable PostgreSQL behavior must preserve the same accounting invariants. The repository's [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md), and [ADR](docs/adr/0001-accounting-authority.md) documents define the transactional and DDD boundaries.

## Security and control posture

Accounting data is high-integrity business evidence. The current design therefore emphasizes:

- tenant- and legal-entity-scoped data boundaries;
- immutable posting and append-only correction paths;
- exact idempotency and payload-evidence binding;
- database-enforced integrity and row-level isolation where applicable;
- traceable reconciliation evidence and exceptions;
- separation of source-system facts from accounting policy decisions;
- fail-closed behavior where required operation authority is not yet implemented;
- no unsupported certification or jurisdictional-compliance claims.

Purpose-bound application authorization for high-impact lifecycle operations remains an open commercialization requirement. Tenant authentication alone must not be represented as permission to complete reconciliation, post, reverse, approve, close, or perform another privileged accounting action. The durable baseline requires the explicit operation→permission contract and trusted identity adapter before those buyer-facing lifecycle routes are treated as complete.

See [`docs/SECURITY.md`](docs/SECURITY.md) and [`docs/OPERABILITY.md`](docs/OPERABILITY.md) for the operational controls and recovery boundaries.

## Standards and research basis

Accounting, data, provenance, architecture, financial-message, database-isolation, and software-engineering decisions are traced in [`docs/doctoring/STANDARD_TRACEABILITY.md`](docs/doctoring/STANDARD_TRACEABILITY.md) with the canonical APA 7 bibliography in [`docs/doctoring/REFERENCES.md`](docs/doctoring/REFERENCES.md). The README intentionally does not duplicate version-specific bibliography entries; for example, the canonical references file carries the repository's current `PostgreSQL 18.6 release notes` citation and `https://www.postgresql.org/docs/release/18.6/` source.

## Documentation map

| Goal | Start here |
| --- | --- |
| Product requirements | [`docs/PRD.md`](docs/PRD.md) |
| Technical requirements | [`docs/TRD.md`](docs/TRD.md) |
| Accounting/billing ownership | [`docs/ACCOUNTING_BOUNDARY.md`](docs/ACCOUNTING_BOUNDARY.md) |
| Architecture | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Data model | [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) |
| Product/technical gaps | [`docs/product-technical-gap-baseline.md`](docs/product-technical-gap-baseline.md) |
| Security | [`docs/SECURITY.md`](docs/SECURITY.md) |
| Operability | [`docs/OPERABILITY.md`](docs/OPERABILITY.md) |
| Test strategy | [`docs/TEST_STRATEGY.md`](docs/TEST_STRATEGY.md) |
| Standards traceability | [`docs/doctoring/STANDARD_TRACEABILITY.md`](docs/doctoring/STANDARD_TRACEABILITY.md) |
| Contributor guidance | [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) |

## Contributing

Preserve the accounting authority boundary when changing contracts or behavior: source systems provide evidence, accounting determines book treatment, and persistence/API/docs/tests must agree on the same invariants. Do not convert a proposed business event, reconciliation suggestion, queued workflow, or synthetic fixture into authoritative accounting truth by documentation alone.

## License

Accounting Information Platform is licensed under the [Apache License 2.0](LICENSE). Third-party dependencies and imported standards/data retain their own commercially compatible terms and attribution requirements.
