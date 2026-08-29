# Test Strategy

## Principle

Accounting evidence is behavioral and exact-head. A string-contract test can protect repository shape, but it does not substitute for a real PostgreSQL transaction, restricted runtime login, package install or security scan when those boundaries are material.

Every behavior change follows RED → GREEN: reproduce the defect with the smallest realistic regression, verify that it fails for the expected reason, implement the narrow causal fix, rerun the focused boundary, then rerun the full applicable gates.

## Test layers

1. **Value contracts** — canonical decimal, currency, code, reference, hash and identifier rules.
2. **Proposal invariants** — required fields, line identity, exact debit / credit balance, tenant scope and immutable source evidence.
3. **Policy resolution** — legal entity, accounting book, fiscal period, effective mapping and currency policy.
4. **Posting behavior** — exact replay, changed-payload conflict, immutable journal identity and authoritative receipt.
5. **Reversal behavior** — equal-and-opposite lines, original preservation, temporal order, occupied-reference protection and command replay/conflict evidence.
6. **Close behavior** — open, soft-close and hard-close state transitions, close idempotency and period-owned database guards.
7. **PostgreSQL invariants** — commit-boundary balance, immutable facts, RLS, tenant binding and restricted runtime roles.
8. **Read models** — trial balance, journals, ledgers, balances, rollforwards, aging, statements, close package, VAT register, bank-statement entries, reconciliation-run evidence, and audit/outbox views tie to the authoritative population.
9. **Integration contracts** — Billing proposal GET/pull envelopes, idempotent retry, origin allowlist and fail-closed remote errors.
10. **Security / abuse** — cross-tenant references, malformed origins, oversized bodies, hostile parser inputs, privilege escalation and replay storms.
11. **Packaging / release** — clean install, wheel install, typing marker, migration install / upgrade, deterministic exact-source provenance, SBOM, protected-head attestations and recovery rehearsals.

## PostgreSQL accounting regressions

Use PostgreSQL 18 integration tests for controls that exist at the storage or role boundary.

Required regressions include:

- direct one-sided journal commit fails through the deferred balance trigger;
- direct line-less journal commit fails and rollback leaves no journal row;
- balanced application posting still commits normally;
- direct update/delete of finalized journal, line, source-reference, reversal, receipt and proposal-source facts fails at the database boundary;
- after an authoritative posting receipt exists, a late journal-line or source-reference insert into that finalized journal fails before it can extend the monetary/evidence population;
- hard-closed periods reject later inserts;
- reconciliation-run command evidence is forced-RLS and immutable, exact retries replay while changed command evidence conflicts, historical runs exclude facts recorded after their knowledge cutoff, and deferred database provenance rejects orphan runs or cross-bank statement bindings;
- reconciliation-match command evidence is forced-RLS and immutable, its run lock prevents a racing terminal transition, source entry/journal cutoffs and CRDT/DBIT cash-line direction are enforced, compound journals use the assigned cash line, and database triggers require one equal allocation per side while rejecting late allocations;
- a login that merely sets `accounting_core.journal_write_role` cannot insert into a soft-closed period;
- a purpose-limited session login that is a member of `accounting_closing_writer` can exercise the supported soft-close exception path;
- migration `0005` changes a pre-existing LOGIN `accounting_closing_writer` back to `NOLOGIN`;
- every tenant-scoped authoritative table both enables and forces RLS;
- a non-owner, non-superuser, non-`BYPASSRLS` runtime login can execute an ordinary supported posting/read path for its bound tenant but cannot read another tenant or acquire owner / administrative authority.

## Bank-statement evidence tests

Issue #7 requires RED → GREEN proof that statement ingest is evidence only:

- a pinned `camt.053.001.14` fixture produces one statement and exact debit/credit entries;
- identical bytes replay without duplicate rows;
- the same idempotency key with changed bytes or the same statement identity with changed material entries fails closed and writes nothing;
- revision mismatch, DTD/external-entity input, bound violations, and malformed decimals persist zero rows;
- cross-tenant assignment/read/write fails, and assignment to a chart account from another book fails at PostgreSQL;
- source artifact → statement → entry provenance is complete, and migration 0011 is required by the foundation loader.

## Fiscal-period-open command tests

Opening a fiscal period is an authoritative state-changing command, so its durable replay evidence is tested independently from period-close state tests. Required coverage proves:

- missing, blank or noncanonical `idempotency_key` and malformed `source_payload_hash` fail before PostgreSQL work;
- an exact retry with the same tenant, command key, immutable source hash, legal entity, period identity and requested dates replays the original open-command result without creating a second evidence row;
- that exact replay remains a replay if the already-opened period has subsequently become `soft_closed` or `hard_closed`; replay must not reopen or otherwise mutate the current period state;
- the same tenant-scoped command key with changed legal-entity scope, period identity, requested dates or source hash fails as an idempotency conflict and writes no second command-evidence row;
- a different command key may acknowledge a matching period only while it is already `open`; it cannot reopen a `soft_closed` or `hard_closed` period;
- migration `0008_fiscal_period_open_command.sql` creates append-only, forced-RLS command evidence, and the foundation migration loader fails closed before connecting when that required migration is absent.

## Reversal state matrix

Reversal tests distinguish fiscal-period state explicitly:

| Period state | Ordinary post | Authorized adjusting / closing | Authorized reversal |
|---|---:|---:|---:|
| `open` | allowed by normal policy | allowed by normal policy | allowed when reversal date and scope are valid |
| `soft_closed` | rejected | allowed only through the purpose-limited closing-writer capability | allowed only through the purpose-limited closing-writer capability |
| `hard_closed` | rejected | rejected | rejected for a new reversal into that locked period |

A reversal date earlier than the original journal accounting date is always rejected.

Exact reversal replay must be tested with tenant, reversal command idempotency identity, original journal reference and immutable command hash. The same identity with a changed reason, changed date or changed original reference must conflict, including when an in-memory receipt cache is absent and the retained reversing journal is the evidence source.

## HomeTax command tests

HomeTax is a write command even though the current implementation fails closed before transport. Tests must prove:

- missing, empty and whitespace-only `idempotency_key` fail before VAT-register or persistence work;
- complete register + missing credential returns a rejected `hometax_credential_missing` receipt;
- complete register + present credential still returns `hometax_transport_unavailable` and performs no network HomeTax call;
- exact key + immutable register evidence replays one stored receipt;
- same key + changed scope / evidence conflicts without a second row;
- incomplete register evidence never persists a sentinel date such as `0001-01-01`.

## Billing pull tests

Billing is a remote source of proposals, not a distributed participant in the accounting transaction.

Tests must prove:

- only the published list envelope is accepted;
- `next_cursor` is null or a valid non-empty cursor string and a non-advancing cursor fails closed;
- malformed host, non-numeric / out-of-range port and malformed IPv6 are accounting validation errors;
- loopback, link-local and `localhost` cannot be re-enabled through the allowlist;
- an initial remote failure writes no proposal from that pull;
- a failure after earlier pages leaves earlier committed postings intact;
- retry replays those postings idempotently and does not duplicate journals;
- the pull is bounded by maximum page count.

## HTTP boundary tests

Request-body and header parsing must fail deterministically before accounting work. Cover missing / duplicate / malformed content-length, non-ASCII numeric forms, oversized bodies, malformed JSON, tenant mismatch and unsupported methods. Error responses identify the caller's next corrective action without leaking credentials or other-tenant existence.

## Supply-chain evidence tests

Pull-request builds must prove the artifact-to-source relationship without accepting GitHub's synthetic PR merge identity as the PR head. The reproducible-build step creates `source-provenance.json` from the verified `EXPECTED_SHA`, source-derived `SOURCE_DATE_EPOCH`, wheel SHA-256 and deterministic SPDX SBOM SHA-256. `SHA256SUMS` covers the wheel, SBOM and source-provenance manifest, and all three remain in the uploaded exact-head evidence bundle.

The PR-capable `accounting-foundation` job must have `contents: read` only. Contract tests reject `id-token: write`, `attestations: write`, or `artifact-metadata: write` in that job because those permissions would be available to repository-controlled tests, build hooks, validation scripts and actions even when individual signing steps are conditionally skipped.

The PR-only `exact-head-dependency-diff` job is a separate fail-closed dependency-security hard gate. It checks out `pull_request.head.sha`, independently fetches the live base branch tip, records the live base SHA, exact head SHA, dependency-manifest name/status diff and manifest SHA-256 values, and rejects a stale/non-ancestor base relationship. The complete hash-locked `requirements-quality.txt` is scanned with a digest-pinned OSV-Scanner container. A known vulnerable dependency, scanner failure, unavailable scanner/evidence path or missing expected evidence is non-passing. Because the current bootstrap base contains no dependency manifests, the foundation PR records both manifests as additions and requires the complete exact-head dependency set to be vulnerability-free instead of manufacturing an empty-base result. The SHA-named artifact retains both the live-base/head evidence record and OSV JSON result.

Organization-level security workflows are supplemental. If an organization dependency-review support probe is forbidden or unsupported and the actual review step is skipped, aggregate workflow success is not accepted as dependency-review evidence; the repository-owned exact-head dependency-diff gate must itself execute and pass.

GitHub OIDC-backed `actions/attest` provenance and SBOM attestations are applicable only to the separate `integrated-attestations` job on `push` builds for `develop` or `main`. That job must be job-level push-only, depend on `accounting-foundation`, download the immutable SHA-named artifact with a full-SHA-pinned action, verify `SHA256SUMS`, verify `source-provenance.json.source_sha == github.sha`, and only then exercise OIDC/attestation write authority. A `pull_request` event can carry a synthetic merge ref/commit in the attestation signing context even when the checked-out build tree is the exact PR head, so that signed statement is not accepted as exact PR-head provenance. After integration, the protected-branch push must rebuild the artifact and the signed attestations must pass on that integrated commit before release. This is evidence readiness; it does not claim a SLSA level or certification.

## Merge gates

One unchanged exact PR head must pass all applicable gates together:

- production statement coverage: **100%**;
- production branch coverage: **100%**;
- public production API docstrings: complete;
- all behavior and real PostgreSQL integration tests;
- repository contracts and compile checks;
- hash-locked package build and installed-public-API smoke test;
- exact-head SAST and security scans;
- executed exact-head dependency-diff security gate against an independently resolved live base tip;
- reproducible wheel, deterministic exact-source provenance manifest, SPDX SBOM and package checksums;
- migration clean-install / upgrade rehearsal;
- qualifying independent approval and zero still-valid unresolved review findings.

Queued, pending, cancelled, absent, neutral, failed, stale, predecessor-head, synthetic merge-ref, status-only or model-only evidence is non-passing. A conditionally scoped gate is evaluated only on the event for which it is applicable; once applicable, a skipped result is non-passing. In particular, signed GitHub artifact attestations are a protected-branch push/release gate, not a substitute for exact PR-head provenance.

## Release / recovery acceptance

Before a release tag, run backup / restore and rollback-strategy rehearsal with production-like PostgreSQL state and the restricted runtime identity. Verify that restored journal, receipt, close and outbox hashes correspond to the exact release source and artifacts. The integrated protected-head push must also reproduce the package and pass OIDC-backed provenance/SBOM attestations for that same integrated commit. A successful CI helper workflow is not itself release evidence if generated changes were not normalized into the canonical source branch.

## Runtime tenant credential isolation

Real PostgreSQL tests create non-owner, non-superuser, non-`BYPASSRLS` runtime logins. A provisioned login must post and read its own tenant, must fail when the application requests a different tenant, and must remain unable to see another tenant even after rewriting the legacy `app.tenant_account_id` custom GUC. A separate unbound runtime login must fail closed. Static migration tests require the session-user/OID binding, locked search path, and migration-loader presence.

## Soft-close command replay regression

Real PostgreSQL tests prove that first soft-close and same-key replay return the same durable source hash/count, a different key conflicts, privileged direct SQL cannot mutate recorded soft-close evidence, and a legacy soft-close with absent evidence fails closed rather than manufacturing a receipt from current ledger state.
