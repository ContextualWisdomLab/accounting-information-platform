# Product and technical gap baseline

**Evidence refresh:** 2026-08-23 (Asia/Seoul)

This file is the durable buyer-visible gap queue for `accounting-information-platform`.
It records authority, dependency order, acceptance evidence, and product gaps that
remain meaningful after an individual commit or workflow run changes. **Live
PR/check evidence is intentionally not duplicated here**: refetch PR #2, issue #1,
the live `develop`/`main` branch state, formal reviews, review threads, and
exact-head workflow jobs before making any merge, release, or readiness decision.
A remembered head, workflow conclusion, local run, predecessor review, or status-only
signal is never a substitute for that fresh evidence.

## Durable product boundary

`accounting-information-platform` is the accounting system of record downstream of
commercial and operational systems. It owns legal entities, accounting books, chart
accounts and mappings, fiscal periods, authoritative balanced journals, reversal
lineage, close, trial balance, statutory/management projections, posting receipts,
and accounting transactional-outbox evidence.

Metering/billing remains authoritative for usage, pricing, invoice intent, payment,
refund, dispute, provider-settlement, and other commercial evidence. It may publish
versioned accounting proposals through the agreed contract, but it may not write
accounting tables directly, select final chart-account identifiers, or claim that a
proposal is a statutory posting.

The current foundation is backend-first: Python domain/reference logic, PostgreSQL
persistence and database-owned invariants, a bounded stdlib HTTP surface, versioned
JSON contracts, and a durable outbox. It does not automatically start a public
listener, ingest or reconcile bank statements, transmit HomeTax/NTS filings, or
provide a controller UI. Those omissions are explicit product scope, not implied
successes.

## Dependency-root order

1. **PR #2 — accounting posting foundation.** This remains the dependency root until
   it is lawfully integrated into `develop`. Before integration, fresh evidence must
   show the unchanged exact PR head, independently resolved live base, applicable
   exact-head CI/security/dependency/package gates, resolved valid review findings,
   and a qualifying independent approval. Missing branch governance is not
   permission to bypass those requirements.
2. **PR #4 — documentation successor.** Its unique documentation value must be
   reconstructed and revalidated from the exact integrated foundation rather than
   merging stale ancestry or transferring predecessor checks/reviews.
3. **Issue #7 — immutable bank-statement evidence registry.** Implement from the
   integrated foundation, preserving the canonical ISO 20022 adapter boundary,
   original-artifact provenance, duplicate-delivery identity, bounded parser
   security, and tenant/book scope.
4. **Issue #8 — deterministic reconciliation and exact book-to-bank bridge.** Build
   on #7; deterministic evidence rules and explicit abstention precede any
   probabilistic/LLM assistance. Split/aggregate matches conserve exact amounts and
   statement lines never post journals automatically.
5. **Issue #6 — bank-reconciliation buyer slice.** Close only after the registry,
   matching/exception/approval workflow, exact bridge, close evidence, and
   provenance are integrated.
6. **Issue #9 — purpose-bound accounting authorization.** Keep tenant identity
   separate from operation authority for posting, reversal, close, tax, outbox,
   audit, and read permissions.

Repository-governance issue #10 is an integration/release prerequisite: the protected
branch policy must enforce the intended review and exact-head gates rather than
leaving merge safety to convention alone.

## Buyer-visible gaps and exit evidence

| Priority | Gap | Buyer impact | Required evidence before closing |
| --- | --- | --- | --- |
| P0 | Foundation is not yet an integrated protected-branch fact | A buyer cannot treat a PR candidate as the accepted accounting authority | Fresh exact head and live base, applicable exact-head gates, qualifying independent approval, lawful protected merge SHA, then post-merge verification on the integrated head |
| P0 | Repository governance does not yet enforce the intended merge/release policy | A technically green candidate could be integrated without durable control-plane enforcement | Protected `develop`/`main` policy with required accounting CI/security/dependency gates, independent review, no force-push/deletion path, and fresh effective-policy evidence |
| P0 | Database authority must remain stronger than application intent | Direct SQL must not rewrite balances, tenant scope, finalized facts, or closed periods | Real PostgreSQL runtime tests for deferred balance, append-only/finalization guards, forced RLS with a restricted runtime login, DB-owned tenant binding, temporal reversal rules, and purpose-limited close authority |
| P0 | Stateful commands require exact replay identity and immutable source evidence | Retries must not duplicate or mutate posting, reversal, close, or tax evidence | Tenant-scoped command keys, immutable source hashes/references, exact replay, changed-evidence conflict, and atomic command/outbox persistence proven in PostgreSQL |
| P1 | Bank evidence registry (#7) is absent | A controller cannot reproduce which statement evidence was authoritative at a knowledge cutoff | Immutable statement/artifact identity, pinned ISO 20022 revision evidence, parser-security limits, duplicate-delivery handling, and tenant/book provenance |
| P1 | Deterministic reconciliation and book-to-bank bridge (#8/#6) are absent | Cash close cannot explain differences or safely abstain from ambiguous matches | Exact split/aggregate conservation, temporal cutoff, concurrency safety, exception/approval workflow, provenance, and bridge equations from bank evidence to posted cash journals |
| P1 | Purpose-bound authorization (#9) is absent | Tenant authentication alone is too coarse for accounting powers | Versioned operation-to-permission mapping, host identity adapter boundary, fail-closed authorization tests, immutable allow/deny audit evidence, and no caller/model-controlled promotion |
| P1 | Production operability and release proof remain incomplete | A buyer cannot yet deploy and recover the service with release-grade evidence | Supported deployment boundary, migration/rollback rehearsal, outbox-drain ownership, metrics/alerts, backup/restore exercise, integrated-head signed attestations, release version, artifact/source hashes, and recovery runbook evidence |
| P2 | No frontend/design-system surface exists | Controllers have no visual close/reconciliation workflow | Introduce Figma, reusable design tokens, Storybook inventory, exact-value tables/exports, and browser accessibility tests only when a UI is actually added |

## Evidence model for the foundation

The foundation candidate is expected to prove the following together on one unchanged
head before it is accepted. The numbers, digests, run identifiers, and commit hashes
are deliberately kept in live PR/issue evidence rather than copied into this file.

- real PostgreSQL integration on the pinned supported major/minor image;
- exact 100% statement and branch coverage for owned production/validator code;
- complete public production API docstrings and deterministic repository contracts;
- database-owned balance, finalization/append-only, tenant-isolation, close, temporal,
  and command-idempotency invariants;
- exact-head SAST, vulnerability/secret/misconfiguration scanning, and dependency
  diff/vulnerability evidence bound to an independently resolved live base;
- reproducible package build, install smoke, deterministic checksums, SPDX SBOM, and
  source-provenance evidence bound to the same exact head;
- no self-mutating repair/normalization workflow in the publishable tree;
- all still-valid review findings resolved and a qualifying independent approval;
- after lawful integration, signed integrated-head provenance/SBOM attestations before
  any version/tag/release claim.

An aggregate workflow conclusion is not enough if a required step is skipped or the
workflow checked out a synthetic merge ref. Likewise, a local test, model review,
status context, predecessor head, or old artifact may inform diagnosis but cannot
satisfy the exact-head release gate.

## Accounting invariants that remain non-negotiable

- Monetary and quantity values that affect journals, balances, reports, or
  reconciliation use exact decimal arithmetic; no binary floating-point accounting.
- A durable journal is non-empty and exactly debit/credit balanced at the database
  commit boundary.
- Finalized journal facts and their source/reversal/receipt evidence are append-only;
  corrections use explicit reversal and reposting.
- Ordinary posting cannot bypass a closed period; limited soft-close exceptions
  require database-owned authorization as well as the matching transaction intent.
- Runtime tenant isolation is derived from database-controlled runtime identity, not
  from a caller-writable session setting or request-body field.
- Commands use tenant-scoped idempotency identity plus immutable source evidence; a
  changed command under the same key fails closed.
- Command outcome and accounting transactional-outbox evidence commit atomically.
- Authoritative relational data stays normalized, tenant-scoped, and uses descriptive
  two-or-more-word `snake_case` object names with effective/system time where policy
  or mappings vary.
- LLM/model output is untrusted interpretation or proposal only. It cannot post a
  journal, approve a reconciliation, or alter accounting policy.

## Bank-reconciliation target after foundation integration

The first buyer-visible reconciliation vertical is deliberately bounded:

```text
immutable bank statement artifact
→ normalized statement / entry identity
→ bank-account ↔ legal-entity / book / cash-account assignment
→ deterministic candidate matching
→ reviewed match or explicit exception
→ exact statement/book bridge
→ close evidence and exportable provenance
```

The initial adapter must pin the supported ISO 20022 message-definition revision and
vendored validation evidence; runtime parsing performs no external schema/entity
fetch. Matching precedence starts with stable provider/end-to-end identities, then
exact amount/currency plus bounded date policy, then approved composite rules, and
otherwise abstains. LLM assistance may summarize or prioritize exceptions but never
consume monetary evidence or approve/post a result.

## Release and acquisition-diligence rule

Do not create a release, version, or tag from a PR candidate. Release evidence must
come from one exact integrated protected head after migration and rollback rehearsal,
backup/restore and operational acceptance, current security/dependency gates,
reproducible package/SBOM/provenance evidence, qualifying review, and any applicable
accessibility acceptance all pass together. `CHANGELOG.md` and artifact/source hashes
must describe that exact integrated release fact.

## Authority and standards traceability

The durable product, technical, security, data, operating, decision, and standards
records remain authoritative in `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/PRD.md`,
`docs/TRD.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `docs/ERD.md`,
`docs/SECURITY.md`, `docs/TEST_STRATEGY.md`, `docs/OPERABILITY.md`, `docs/adr/`, and
`docs/doctoring/`. Current international/accounting technical decisions belong in the
APA 7 bibliography and standards traceability records; this gap baseline does not
claim certification or compliance on their behalf.
