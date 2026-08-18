# Accounting Information Platform

The **Accounting Information Platform** is CWL's authoritative accounting and financial-reporting control plane. It sits downstream of operational and commercial systems and upstream of trial balance, close, consolidation, and financial reporting.

The platform receives evidence-backed journal proposals, resolves legal entity, accounting book, chart accounts, fiscal period, currency, and policy in this boundary, and returns the only authoritative `posted`, `held`, `rejected`, or `reversed` receipt.

## Authority boundary

```text
CWL products
  -> source facts
  -> Metering Billing Platform
  -> accounting_journal_proposal
  -> Accounting Information Platform
  -> posted journal / hold / rejection / reversal
  -> accounting_posting_receipt
  -> trial balance and financial reporting
```

The Metering Billing Platform owns usage, pricing, invoice intent, collections, refunds, provider settlement, and commercial reconciliation. This platform owns legal books, posted journals, fiscal-period control, chart-account resolution, reversals, trial balances, and financial-statement projections. A balanced proposal is not a posted journal. Source systems may propose semantic account roles; they may not select final chart-account identifiers or claim posting.

## What operators can do now

The current release includes a dependency-free reference core, a PostgreSQL 18 posting adapter, versioned contracts, and a purpose-limited HTTP surface for proposal intake, close, inquiry, and statement projection.

Controllers, accounting operations, and finance-platform operators can:

- accept a versioned `accounting_journal_proposal` from an approved source, including Billing `validated` and `exported` proposals;
- distinguish exact replay from conflicting reuse of an idempotency key;
- resolve tenant, legal entity, accounting book, fiscal period, currencies, account roles, and policy versions;
- post a balanced immutable journal or receive a structured hold or rejection;
- reverse a posted journal with an equal-and-opposite journal while preserving both records;
- soft-close and hard-close fiscal periods, including an AIS closing journal into retained earnings on hard-close;
- produce trial balances, account balances, ledgers, rollforwards, and receivable aging that tie to the included journal population;
- read income statement, balance sheet, changes in equity, and cash-flow projections for a book and period;
- return an authoritative `accounting_posting_receipt` (`posted`, `held`, `rejected`, or `reversed`).

Invoice issuance, payment capture, and provider payout do not by themselves determine revenue recognition. Performance obligation, principal-versus-agent, variable consideration, and period-of-recognition policy remain in this boundary.

## Documentation

| Topic | Document |
|---|---|
| Architecture views and deployment sequence | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Billing versus accounting ownership | [docs/ACCOUNTING_BOUNDARY.md](docs/ACCOUNTING_BOUNDARY.md) |
| Accepted architecture decisions | [docs/adr/](docs/adr/) |
| Standards bibliography | [docs/doctoring/REFERENCES.md](docs/doctoring/REFERENCES.md) |

Related product records: [docs/PRD.md](docs/PRD.md), [docs/TRD.md](docs/TRD.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md), [docs/SECURITY.md](docs/SECURITY.md), [docs/OPERABILITY.md](docs/OPERABILITY.md), and [docs/doctoring/STANDARD_TRACEABILITY.md](docs/doctoring/STANDARD_TRACEABILITY.md). Local validation commands are in [CONTRIBUTING.md](CONTRIBUTING.md).
