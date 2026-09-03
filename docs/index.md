# Accounting Information Platform

The Accounting Information Platform is ContextualWisdomLab's statutory accounting boundary. It consumes evidence-backed journal proposals from approved source systems, resolves legal entity, accounting book, chart accounts, fiscal period, currency, and policy, and returns authoritative posting evidence without letting source systems write the ledger directly.

> Status: pre-alpha foundation. This page describes protected `develop` truth and does not promote active pull requests, queued checks, or unreleased accounting capabilities to shipped behavior.

## Start here

- [Repository overview and independent-run guide](https://github.com/ContextualWisdomLab/accounting-information-platform#readme)
- [Product requirements](PRD.md)
- [Technical requirements](TRD.md)
- [Architecture](ARCHITECTURE.md)
- [Accounting and billing boundary](ACCOUNTING_BOUNDARY.md)
- [Data model](DATA_MODEL.md)
- [Security](SECURITY.md)
- [Operability](OPERABILITY.md)
- [Test strategy](TEST_STRATEGY.md)
- [Architecture decisions](adr/0001-accounting-authority.md)
- [Product and technical gap baseline](product-technical-gap-baseline.md)
- [Repository releases](https://github.com/ContextualWisdomLab/accounting-information-platform/releases)

## Product responsibility

This repository owns legal books, posted journals, fiscal-period control, chart-account resolution, accounting policy, reversals, trial balances, financial reporting projections, accounting posting receipts, and the evidence needed to explain those outcomes. Upstream commercial systems describe economic events through published proposal contracts; they do not choose final chart accounts or write accounting tables.

The Metering Billing Platform remains responsible for usage, pricing, invoice intent, collections, refunds, provider settlement, and commercial reconciliation. Composition hubs and other sibling products may consume this platform through versioned contracts, but they are not runtime prerequisites for the accounting authority itself.

## Authority model

A balanced journal proposal is not a posted journal. The Accounting Information Platform validates proposal identity and policy, resolves accounting-owned references, applies idempotency and period controls, persists immutable posting/reversal evidence, and only then emits an authoritative receipt. Bank-statement and reconciliation evidence are accounting inputs; they cannot silently acquire posting or close authority.

## Verification and operations

The repository validates its Python package, schemas, PostgreSQL behavior, migrations, security boundaries, exact-decimal accounting invariants, idempotency, coverage, package build, and documentation contracts through protected integration workflows. Current-head checks and counted reviews are the integration evidence boundary; predecessor-head, queued, skipped, or model-only results are not treated as shipped proof.

Operational documentation covers independent execution, PostgreSQL-backed behavior, security, readiness, recovery expectations, and ecosystem boundaries. Production compliance or jurisdiction-specific certification is not implied by the source tree alone.

## Publication boundary

This file is a GitHub Pages source candidate, not evidence that Pages is already live. Publication is complete only after protected integration, owner-side Pages configuration/deployment, and live HTTPS content verification succeed.

## License

Accounting Information Platform source is licensed under the [Apache License 2.0](https://github.com/ContextualWisdomLab/accounting-information-platform/blob/develop/LICENSE). Third-party dependencies retain their own license obligations.
