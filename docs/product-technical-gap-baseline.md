# Product and technical gap baseline

**Evidence refresh:** 2026-08-27 (Asia/Seoul)

This file is the durable buyer-visible gap queue for `accounting-information-platform`.
It records authority, dependency order, acceptance evidence, and product gaps that
remain meaningful after an individual commit or workflow run changes. **Live
PR/check evidence is intentionally not duplicated here**: refetch every open pull
request, issue, the live `develop`/`main` branch state, formal reviews, review
threads, and exact-head workflow jobs before making any merge, release, or
readiness decision. A remembered head, workflow conclusion, local run,
predecessor review, or status-only signal is never a substitute for that fresh
evidence.

## Durable product boundary

`accounting-information-platform` is the accounting system of record downstream of
commercial and operational systems. It owns legal entities, accounting books, chart
accounts and mappings, fiscal periods, authoritative balanced journals, reversal
lineage, close, trial balance, statutory/management projections, posting receipts,
accounting transactional-outbox evidence, and accepted immutable bank-statement
evidence with its normalized entries.

Metering/billing remains authoritative for usage, pricing, invoice intent, payment,
refund, dispute, provider-settlement, and other commercial evidence. It may publish
versioned accounting proposals through the agreed contract, but it may not write
accounting tables directly, select final chart-account identifiers, or claim that a
proposal is a statutory posting.

The current foundation is backend-first: Python domain/reference logic, PostgreSQL
persistence and database-owned invariants, a bounded stdlib HTTP surface, versioned
JSON contracts, and a durable outbox. The accounting posting foundation and the
immutable `camt.053.001.14` bank-statement evidence registry are integrated protected
`develop` facts. The platform does not yet reconcile statements to journals,
transmit HomeTax/NTS filings, enforce purpose-bound application authorization, or
provide a controller UI. Those omissions are explicit product scope, not implied
successes.

## Open work inventory

| Item | Durable role | State expectation before it can close |
| --- | --- | --- |
| Accounting posting foundation | Dependency root for every later slice | Integrated into protected `develop`; post-integration signed provenance/SBOM attestations must stay green on the integrated head before any release claim |
| Immutable bank-statement evidence registry | First buyer-visible reconciliation input; implements the statement-evidence issue | Integrated into protected `develop`; exact replay, changed-hash conflict, parser fail-closed behavior, tenant isolation, and assignment command identity remain protected-branch invariants |
| Documentation successor | Customer/operator README plus ADR enrichment rebuilt from the integrated foundation | Reconstructed from the exact integrated tree after the dependency roots land; stale ancestry must not merge |
| Deterministic reconciliation and book-to-bank bridge | Second bounded reconciliation slice | Delivered on protected `develop`: deterministic proposal engine, exact bridge with finite-`Decimal` boundary, immutable-scope close-review projection, and the durable `reconciliation_run`/`reconciliation_exception`/`reconciliation_evidence` substrate with forced tenant RLS and immutable run scope |
| Bank-reconciliation buyer slice | Close-out of the reconciliation vertical | Closed only after candidate/match allocation conservation, exception/approval workflow, and close-package provenance are integrated on top of the delivered substrate |
| Purpose-bound accounting authorization | Least-privilege operation authority | Versioned permission model with fail-closed decisions and immutable audit evidence |
| Branch/release governance | Integration and release control plane | Protected `develop`/`main` effective policy including repository-owned exact-head accounting CI, independent reviews, and no normal force-push/deletion/bypass path |

Issue numbering belongs to live tracker state; this table records durable roles so
that renumbering cannot silently drop a commitment.

## Dependency-root order

1. **Accounting posting foundation.** Integrated into protected `develop` after
   all exact-head gates — repository CI, SAST/security, dependency evidence,
   CodeRabbit current-head status, and Strix — passed together on one unchanged
   head. Post-integration signed provenance/SBOM attestations run on the
   integrated-head push before any release/tag claim.
2. **Immutable bank-statement evidence registry.** Integrated into protected
   `develop`; preserve its exact replay, changed-hash conflict, parser fail-closed,
   tenant-isolation, assignment-command identity, direction-aware counterparty,
   and currency-scope invariants on every later integrated head.
3. **Documentation successor rebuild.** Its unique documentation value must be
   reconstructed and revalidated from the exact integrated foundation rather than
   merging stale ancestry or transferring predecessor checks/reviews.
4. **Deterministic reconciliation and exact book-to-bank bridge.** Build on the
   integrated registry; deterministic evidence rules and explicit abstention
   precede any probabilistic/LLM assistance. Split/aggregate matches conserve
   exact amounts and statement lines never post journals automatically. The exact
   bridge is integrated protected `develop` fact including its runtime
   finite-`Decimal` monetary-domain boundary (binary float, `NaN`, and infinities
   fail closed before bridge arithmetic; finite signed balances and movements
   remain valid because populations may be signed; ADR 0054).
5. **Bank-reconciliation buyer-slice close-in.** Close only after candidate/match
   allocation conservation, exception/approval workflow, exact bridge, close
   evidence, and provenance are integrated on top of the delivered substrate.
   Delivered on `develop`: read-only close-review projection (exact
   bank/book/reconciled/outstanding/unexplained values, immutable run and
   population provenance, unresolved statement-entry references, preceding-run
   deltas scoped to the same accounting scope, JSON/CSV decimal-string exports,
   and evidence-eligibility-only `suitable_for_period_close_review`) plus the
   durable `reconciliation_run`/`reconciliation_exception`/
   `reconciliation_evidence` rows with forced tenant RLS and the immutable
   evaluated-run scope guard. Open: many-to-many exact allocation conservation,
   candidate/match persistence, and reconciliation approval with close-package
   provenance.
6. **Purpose-bound accounting authorization.** Keep tenant identity separate from
   operation authority for posting, reversal, close, tax, outbox, audit, and read
   permissions.

Repository-governance work is an integration/release prerequisite running across
all of the above: the protected branch policy must enforce the intended review and
exact-head gates rather than leaving merge safety to convention alone.

## Buyer-visible gaps and exit evidence

| Priority | Gap | Buyer impact | Required evidence before closing |
| --- | --- | --- | --- |
| P0 | Repository governance does not yet enforce the intended merge/release policy everywhere | A technically green candidate could be integrated without durable control-plane enforcement, and `main` remains outside release-grade protection | Protected `develop`/`main` policy with required accounting CI/security/dependency gates, independent review, thread resolution, no force-push/deletion path, and fresh effective-policy evidence from branch and ruleset surfaces together |
| P0 | Database authority must remain stronger than application intent on the integrated head | Direct SQL must never rewrite balances, tenant scope, finalized facts, or closed periods | Real PostgreSQL runtime tests for deferred balance, append-only/finalization guards, forced RLS with a restricted runtime login, DB-owned tenant binding, temporal reversal rules, and purpose-limited close authority |
| P0 | Stateful commands require exact replay identity and immutable source evidence | Retries must not duplicate or mutate posting, reversal, close, tax, or statement-acceptance evidence | Tenant-scoped command keys, immutable source hashes/references, exact replay, changed-evidence conflict, and atomic command/outbox persistence proven in PostgreSQL |
| P1 | Deterministic reconciliation and candidate/match allocation are partially delivered | Cash close can now explain differences and safely abstain, but approved candidates with exact split/aggregate conservation are not yet persistent across runs | Exact split/aggregate conservation with many-to-many allocation rows, temporal cutoff, concurrency safety, exception/approval workflow, provenance, and bridge equations from bank evidence to posted cash journals. Delivered so far: deterministic proposal engine, finite-Decimal bridge, immutable-scope close-review projection, and the durable run/exception/evidence substrate on protected `develop` |
| P1 | Close-review projection integration is in flight | Controllers cannot yet read an exact, exportable close-review projection from one integrated head | The read-only projection and its authority/export contracts are integrated; it cannot approve or post. Outstanding: close-review opened only from integrated projection and its restacked successors |
| P1 | Purpose-bound authorization is absent | Tenant authentication alone is too coarse for accounting powers | Versioned operation-to-permission mapping, host identity adapter boundary, fail-closed authorization tests, immutable allow/deny audit evidence, and no caller/model-controlled promotion |
| P1 | Production operability and release proof remain incomplete | An operator cannot yet deploy, observe, back up, and recover the service with release-grade evidence | Supported deployment boundary, migration/rollback rehearsal, outbox-drain ownership, metrics/alerts, backup/restore exercise, integrated-head signed attestations, release version, artifact/source hashes, and recovery runbook evidence |
| P2 | No frontend/design-system surface exists | Controllers have no visual close/reconciliation workflow | Introduce Figma source of truth, reusable design tokens, Storybook inventory with scene/edge-case event definitions, exact-value tables/exports, and browser accessibility tests only when a UI is actually added |

## Evidence model for integration

Each integration candidate is expected to prove the following together on one
unchanged head before it is accepted. The numbers, digests, run identifiers, and
commit hashes are deliberately kept in live PR/issue evidence rather than copied
into this file.

- real PostgreSQL integration on the pinned supported major/minor image;
- exact 100% statement and branch coverage for owned production/validator code;
- complete public production API docstrings and deterministic repository contracts;
- database-owned balance, finalization/append-only, tenant-isolation, close,
  temporal, and command-idempotency invariants;
- exact-head SAST, vulnerability/secret/misconfiguration scanning, and dependency
  diff/vulnerability evidence bound to an independently resolved live base;
- reproducible package build, install smoke, deterministic checksums, SPDX SBOM,
  and source-provenance evidence bound to the same exact head;
- no self-mutating repair/normalization workflow in the publishable tree;
- all still-valid review findings resolved and qualifying independent approvals;
- after lawful integration, signed integrated-head provenance/SBOM attestations
  before any version/tag/release claim.

An aggregate workflow conclusion is not enough if a required step is skipped or
the workflow checked out a synthetic merge ref. Likewise, a local test, model
review, status context, predecessor head, or old artifact may inform diagnosis but
cannot satisfy the exact-head release gate.

## Accounting invariants that remain non-negotiable

- Monetary and quantity values that affect journals, balances, reports, or
  reconciliation use exact decimal arithmetic; no binary floating-point accounting.
- A durable journal is non-empty and exactly debit/credit balanced at the database
  commit boundary.
- Finalized journal facts and their source/reversal/receipt evidence are
  append-only; corrections use explicit reversal and reposting.
- Ordinary posting cannot bypass a closed period; limited soft-close exceptions
  require database-owned authorization as well as the matching transaction intent.
- Runtime tenant isolation is derived from database-controlled runtime identity,
  not from a caller-writable session setting or request-body field.
- Commands use tenant-scoped idempotency identity plus immutable source evidence;
  a changed command under the same key fails closed.
- Command outcome and accounting transactional-outbox evidence commit atomically.
- Authoritative relational data stays normalized, tenant-scoped, and uses
  descriptive two-or-more-word `snake_case` object names with effective/system
  time where policy or mappings vary.
- LLM/model output is untrusted interpretation or proposal only. It cannot post a
  journal, approve a reconciliation, choose a chart account, consume a monetary
  amount, or alter accounting policy.
- A bank-statement entry never posts, reverses, approves, or mutates a journal by
  itself; unmatched evidence becomes an exception or an explicit adjusting-journal
  proposal reviewed under authority.

## Bank-reconciliation target after foundation integration

The first buyer-visible reconciliation vertical is deliberately bounded:

```text
immutable bank statement artifact
→ normalized statement / entry identity          [delivered by the registry slice]
→ bank-account ↔ legal-entity / book assignment  [delivered by the registry slice]
→ deterministic candidate matching               [delivered: proposal engine]
→ exact book-to-bank bridge                      [delivered; finite-Decimal boundary + bridge scope in ADR 0054]
→ durable run / exception / evidence rows        [delivered; forced RLS + run-scope guard]
→ close-review projection                        [delivered; evidence eligibility only, same-scope deltas]
→ candidate/match allocation conservation        [open M2 slice]
→ reconciliation approval and close package      [open M2 slice]
```

The delivered adapter pins the supported ISO 20022 message-definition revision and
vendored validation evidence; runtime parsing performs no external schema/entity
fetch and fails closed on revision drift, entity expansion, unbounded depth, or
non-canonical decimals. Matching precedence starts with stable provider/end-to-end
identities, then exact amount/currency plus bounded date policy, then approved
composite rules, and otherwise abstains. LLM assistance may summarize or prioritize
exceptions but never consumes monetary evidence or approves/posts a result.

## Release and diligence rule

Do not create a release, version, or tag from a PR candidate. Release evidence
must come from one exact integrated protected head after migration and rollback
rehearsal, backup/restore and operational acceptance, current security/dependency
gates, reproducible package/SBOM/provenance evidence, qualifying review, and any
applicable accessibility acceptance all pass together. `CHANGELOG.md` and
artifact/source hashes must describe that exact integrated release fact.

## Authority and standards traceability

The durable product, technical, security, data, operating, decision, and standards
records remain authoritative in `README.md`, `AGENTS.md`, `CLAUDE.md`,
`docs/PRD.md`, `docs/TRD.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`,
`docs/ERD.md`, `docs/SECURITY.md`, `docs/TEST_STRATEGY.md`, `docs/OPERABILITY.md`,
`docs/adr/`, and `docs/doctoring/`. Current international/accounting technical
decisions belong in the APA 7 bibliography and standards traceability records,
including the ISO 20022 message-definition citations backing the statement
adapter; this gap baseline does not claim certification or compliance on their
behalf.
