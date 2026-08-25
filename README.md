# Accounting Information Platform

The Accounting Information Platform is Contextual Wisdom Lab's statutory
accounting boundary. It consumes evidence-backed journal proposals from billing
and other source systems, resolves legal entity, accounting book, chart
accounts, fiscal period, currency, and policy, and returns an authoritative
posting receipt. A balanced proposal is not a posted journal.

This repository is a separate deployable product. It does not require
[Naruon](https://github.com/ContextualWisdomLab/naruon), the Metering Billing
Platform, or any other sibling checkout to exist, clone, or run. Naruon is the
CWL composition hub that may later receive this product; composition is
optional and is not a runtime dependency.

## Current readiness

This tree is the first executable foundation (Python package
`accounting-information-platform` 0.1.0, Development Status: Pre-Alpha). It is
not a hosted service and does not start an HTTP listener.

What is present:

- a dependency-free Python reference core for exact-decimal proposal
  validation, policy checks, idempotent posting, append-only reversal, and
  trial-balance aggregation;
- closed JSON Schema Draft 2020-12 contracts for the journal proposal, policy
  manifest, and posting receipt;
- a PostgreSQL 18.4-oriented normalized migration with tenant-scoped foreign
  keys and row-level security;
- `PostgresPostingLedger` and `apply_foundation_migration`, which persist the
  same posting, replay, reversal, and trial-balance invariants through that
  migration in one commit boundary;
- a stdlib HTTP surface for proposal acceptance, posting receipts, journals,
  reversals, period close/open, trial balances, financial statements, ledgers,
  aging, VAT/HomeTax rejection receipts, outbox, audit, and catalog reads;
- product, architecture, security, and standards documents listed below.

What is not present: an automatically started listener, gRPC or live event
transport, foreign exchange, revenue schedules, bank-statement ingestion and
reconciliation, consolidation, tax calculation, or live HomeTax/NTS
transmission. The outbox is durable but this tree does not publish it to a
bus. The in-memory `PostingLedger` remains the reference oracle that the
PostgreSQL adapter must match.

The initial milestone does not claim production compliance with a
jurisdiction's accounting, tax, or statutory reporting rules. It establishes
controls and traceability needed to implement reviewed policies without
changing the journal authority model.

## What the product does

Finance teams can take a versioned `accounting_journal_proposal` from an
approved source, detect exact replay versus conflicting reuse of an
idempotency key, resolve tenant / legal entity / book / period / currency /
account-role / policy versions, post a balanced immutable journal or fail
closed, reverse without destroying the original, produce a trial balance that
ties to the included journal population, and return an
`accounting_posting_receipt`.

The current reference core posts a valid proposal or raises
`AccountingValidationError` / `IdempotencyConflictError`. The posting-receipt
contract also enumerates `held`, `rejected`, and `reversed` for the service
milestone. Reversal in the reference core appends an equal-and-opposite
journal and preserves lineage; it does not update or delete the original.

Source systems describe economic events. This platform determines book
treatment. Invoice issuance, payment capture, and provider payout do not by
themselves determine revenue recognition.

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

The Metering Billing Platform owns usage, pricing, invoice intent,
collections, refunds, provider settlement, commercial reconciliation, and the
authoritative `accounting_journal_proposal` schema. This repository owns legal
books, posted journals, fiscal-period control, chart-account resolution,
reversals, trial balances, the `accounting_policy_manifest` schema, and the
`accounting_posting_receipt` schema.

A proposal may name semantic roles such as `accounts_receivable` or
`usage_revenue`. Accounting maps those roles to chart accounts under an
effective policy version. Source systems cannot bypass that mapping by sending
chart-account identifiers or by writing journal tables.

## Independent run

The foundation runs from this repository alone. Do not clone Naruon, the
Metering Billing Platform, or any other sibling to verify it.

Requires Python 3.13+. Quality tooling is hash-locked in
`requirements-quality.txt`. The reference core has no runtime package
dependencies.

```bash
python3 -m pip install --only-binary=:all: --require-hashes -r requirements-quality.txt
PYTHONPATH=src:. python3 -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src:. python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage report --fail-under=100 --show-missing
python3 scripts/validate_repository.py .
python3 -m compileall -q src scripts tests
python3 -m pip install --no-deps --no-build-isolation -e .
python3 -m pip wheel --no-deps --no-build-isolation -w dist .
```

These commands do not automatically open an application HTTP port and do not
contact Naruon. To embed the stdlib HTTP surface, call the exported server
factory/runner and provide the tenant-bound host boundary explicitly.
`unittest` discovery also runs `tests/test_postgres_posting.py`, which needs a
reachable PostgreSQL 18 instance and `ACCOUNTING_DATABASE_URL` (CI uses
`postgresql://postgres:postgres@127.0.0.1:5432/accounting_test` and applies
the checked-in migration chain through `database/migrations/0010_soft_close_command_evidence.sql`). Persistence is still
local to this repository; it is not a Naruon or sibling checkout.

Optional import smoke after the editable install above:

```bash
python3 -c "import accounting_information_platform as aip; assert aip.PostingLedger.__name__ == 'PostingLedger'"
```

## How a sibling calls this platform

The stdlib HTTP surface is available for a host to mount; no listener starts
automatically, and no gRPC or live event transport is published. When a
sibling—including Naruon as composition hub—integrates, it uses the versioned
file contracts in `schemas/` and the documented HTTP/in-process boundary rather
than a private table or undeclared payload.

| Contract | Authority | Path |
|---|---|---|
| Journal proposal | Metering Billing Platform | `schemas/accounting-journal-proposal.schema.json` |
| Policy manifest | Accounting Information Platform | `schemas/accounting-policy-manifest.schema.json` |
| Posting receipt | Accounting Information Platform | `schemas/accounting-posting-receipt.schema.json` |

Contract identities (`$id`) are schema identifiers, not hosted URLs:

- `https://schemas.contextualwisdomlab.org/metering-billing/accounting-journal-proposal/v1`
- `https://schemas.contextualwisdomlab.org/accounting/policy-manifest/v1`
- `https://schemas.contextualwisdomlab.org/accounting/posting-receipt/v1`

Call rules that already exist in those contracts and the reference core:

1. Submit a balanced `accounting_journal_proposal` with an idempotency key,
   SHA-256 source-payload hash, tenant and legal-entity URNs, semantic account
   roles, and proposal status in `draft` / `validated` / `exported` /
   `rejected`. The proposal status enumeration does not include `posted`.
2. This platform resolves policy and chart accounts, then returns an
   `accounting_posting_receipt`. Only a receipt may carry `posted`, `held`,
   `rejected`, or `reversed`.
3. Exact replay of the same idempotency key and payload hash returns the
   original receipt. Reuse of the key with a different hash fails closed.
4. Naruon, billing, or any other sibling may compose this product by producing
   the proposal contract and consuming the receipt contract. They do not write
   journals, choose final chart-account identifiers, or require this process to
   start inside Naruon.

The in-process calls are `PostingLedger.post(proposal, policy)` (reference
core) and
`PostgresPostingLedger.post(proposal, policy)` (durable). Both also expose
`reverse(...)` and `trial_balance(...)`. A transactional outbox row is written
in the PostgreSQL posting transaction; nothing in this tree publishes those
events onto a live bus. The same boundary exposes `POST /journals` for
AIS-owned adjustments and `GET /financial-statements`, `GET /trial-balances`,
`GET /account-ledgers`, and the aging/reporting routes for buyer-facing reads.

## Standards already cited

The product documents already trace these authorities. Citations follow APA
7th as recorded in [`docs/doctoring/REFERENCES.md`](docs/doctoring/REFERENCES.md).
This README does not add literature beyond that set.

Cloud Native Computing Foundation. (2022). *CloudEvents specification, version 1.0.2*. https://github.com/cloudevents/spec

Financial Accounting Standards Board. (2024). *Accounting Standards Codification Topic 606: Revenue from contracts with customers*. https://asc.fasb.org/topic&trid=2121986

IFRS Foundation. (2024). *IFRS 18 presentation and disclosure in financial statements*. https://www.ifrs.org/projects/completed-projects/2024/primary-financial-statements/

IFRS Foundation. (2024). *Post-implementation review of IFRS 15 revenue from contracts with customers: Project summary and feedback statement*. https://www.ifrs.org/projects/completed-projects/2024/pir-ifrs-15/

International Organization for Standardization. (2026). *ISO 20022-1:2026 financial services—Universal financial industry message scheme—Part 1: Metamodel* (3rd ed.). https://www.iso.org/standard/20022-1

International Organization for Standardization. (2022). *ISO/IEC/IEEE 42010:2022 software, systems and enterprise—Architecture description*. https://www.iso.org/standard/74393.html

Internet Engineering Task Force. (2024). *Universally unique IDentifiers (UUIDs)* (RFC 9562). https://www.rfc-editor.org/rfc/rfc9562

PostgreSQL Global Development Group. (2026). *PostgreSQL 18.4 release notes*. https://www.postgresql.org/docs/release/18.4/

World Wide Web Consortium. (2013). *PROV-O: The PROV ontology*. https://www.w3.org/TR/prov-o/

XBRL International. (2003). *XBRL 2.1 specification*. https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html

How those authorities map to current product decisions is in
[`docs/doctoring/STANDARD_TRACEABILITY.md`](docs/doctoring/STANDARD_TRACEABILITY.md).

## Product documents

- [Product requirements](docs/PRD.md)
- [Technical requirements](docs/TRD.md)
- [Accounting and billing boundary](docs/ACCOUNTING_BOUNDARY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Product and technical gap baseline](docs/product-technical-gap-baseline.md)
- [Data model](docs/DATA_MODEL.md)
- [Security](docs/SECURITY.md)
- [Operability](docs/OPERABILITY.md)
- [Test strategy](docs/TEST_STRATEGY.md)
- [Architecture decisions](docs/adr/0001-accounting-authority.md)
- [Contributor and agent operations](docs/CONTRIBUTING.md)

## License

Apache License 2.0. See [LICENSE](LICENSE).
