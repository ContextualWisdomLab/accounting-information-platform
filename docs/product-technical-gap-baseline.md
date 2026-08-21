# Product and technical gap baseline

**Evidence date:** 2026-08-21 (Asia/Seoul)

This baseline separates observed repository and GitHub state from proposed
work. It is the working queue for the buyer-visible product loop; it does not
claim that a local change is merged or that a queued remote check is live
success.

## Observed current state

- The protected default branch is `develop`; the repository was bootstrapped
  from an empty tree and the accounting foundation is currently developed on
  PR #2 (`feat: establish accounting posting foundation`).
- PR #2 is open and Ready for review. The production implementation candidate
  is exact commit `a8fb3a61a3045b7d9fc6f9a35156c530ffe0a0f1`; later branch
  commits only refresh this gap baseline. It is based on `develop` head
  `66800a2f4e849ec8a0b060f9603f6667803284b4`; GitHub reports it `MERGEABLE`,
  with no qualifying approval and no protected merge SHA.
  Accounting Foundation CI, security/SAST, scheduler, Noema/OpenCode, and
  Strix checks are currently queued or pending on this exact head. CodeRabbit
  and Devin review are also pending after the Ready transition. Review threads
  still require the formal GitHub resolution/approval gate.
- PR #4 is open and Draft at exact head
  `4a38cdc4b4044d105ec17c5f97ef1e8ba17a1b7c`, based on stale foundation head
  `d82c2e9b9a265ea8ea424da8e29b2a409e2d6e42`; GitHub reports mergeability
  `UNKNOWN` and `REVIEW_REQUIRED`. Re-sync it to the integrated foundation
  and retarget it to `develop` only after PR #2 is protected-merged.
  PR #5 is merged documentation for the buyer/operator README.
- Open product issues are #1 (foundation), #6 (book-to-bank reconciliation),
  #7 (immutable ISO 20022 statement evidence), #8 (deterministic matching and
  bridge), and #9 (purpose-bound accounting authorization). #7 must precede
  #8, and #2 must precede the reconciliation slices and #9.
- The current product is a backend accounting boundary with a dependency-free
  Python reference core, PostgreSQL persistence, a stdlib HTTP surface, JSON
  contracts, and a durable outbox. It does not automatically start a listener,
  publish live events, ingest bank statements, reconcile bank evidence, or
  transmit HomeTax/NTS filings.

## Buyer-visible gaps and exit evidence

| Priority | Gap | Buyer impact | Required evidence before closing |
| --- | --- | --- | --- |
| P0 | Foundation merge gate is not closed | A buyer cannot treat the ledger as an accepted authoritative boundary | Exact current PR head, required Checks green, independent qualifying approval, protected merge SHA, and a post-merge verification run |
| P0 | Database authority must remain stronger than application intent | Direct SQL could otherwise alter balances, tenant scope, or closed periods | PostgreSQL 18 runtime tests for deferred balance enforcement, append-only journal mutation rejection, forced RLS, non-owner/non-superuser login behavior, and purpose-limited close authority |
| P0 | HomeTax command replay identity was incomplete at the prior PR head | Retry could create duplicate rejected filing evidence or accept changed evidence under one command | Current implementation requires tenant-scoped `idempotency_key`, stores the register hash, replays the original row, and rejects changed evidence/scope; remote exact-head Checks must verify it |
| P1 | Bank evidence registry (#7) is absent | A controller cannot reproduce which bank statement bytes were authoritative at a knowledge cutoff | Immutable `camt.053` registry with version/generation/hash provenance, parser-security tests, duplicate-delivery handling, and tenant/book scope |
| P1 | Deterministic reconciliation and book-to-bank bridge (#8/#6) are absent | Cash close cannot explain differences or safely abstain from ambiguous matches | Exact split/aggregate conservation, temporal cutoff, concurrency, exception/approval workflow, and bridge equations from authoritative statement and posted-journal rows |
| P1 | Purpose-bound authorization (#9) is absent | Tenant authentication alone could imply posting, close, tax, outbox, or audit power | Versioned operation-to-permission mapping, Keyverse/OIDC boundary, fail-closed HTTP tests, denial audit evidence, and no caller-controlled promotion |
| P1 | Production operability and release proof are absent | A buyer cannot yet deploy this as a supported service with measurable recovery | Supported listener host contract, migrations/rollback policy, outbox drain ownership, metrics/alerts, backup/restore exercise, release version, and `CHANGELOG.md` evidence |
| P2 | No frontend/design-system surface exists | Controllers have no visual close/reconciliation workflow yet | Add Figma file ID, design tokens, Storybook inventory, and browser interaction/accessibility tests only when a UI is actually introduced |

## Current verification evidence

- Local Python 3.13.14 with hash-locked quality dependencies was used; no
  system-runtime dependency was added.
- At exact candidate `a8fb3a6`, real PostgreSQL 18.4 on `127.0.0.1` was used
  with the local `seonghobae` test role and `accounting_test` database. The
  full suite passed: 248 tests,
  including deferred-balance, finalized-ledger immutability, forced runtime
  RLS, HTTP, HomeTax period-end fallback, explicit reversal-command replay,
  source-provenance rejection, temporal ordering, PostgreSQL concurrency
  locks, and PostgreSQL integration coverage.
- Branch and statement coverage passed at 100%: 3,683 statements, 1,362
  branches, zero misses or partial branches. This is local evidence, not
  remote Checks evidence.
- `scripts/validate_repository.py`, `compileall`, and `git diff --check`
  passed locally. Migration `0006_concurrency_hot_partition.sql` was applied
  by the real PostgreSQL test setup and the temporary self-mutating repair
  workflow remains removed.
- The runtime uses `ThreadingHTTPServer`, bounded `lock_timeout` and idle
  transaction timeout, tenant/command and shared-period advisory locks, close
  period row locking, and tenant-leading hot-write indexes. Physical
  hash-by-tenant/time partitioning is intentionally not implemented; ADR 0050
  records it as a measured follow-up because partitioned unique/foreign-key
  identity must be redesigned together.
- The local supply-chain evidence run produced two reproducible wheels with
  identical SHA-256 digests, an SPDX 2.3 SBOM, and verified `SHA256SUMS`
  output. This does not replace the remote attestation and artifact checks.
- The normal branch push was accepted, but GitHub reported that branch-update
  rule violations were bypassed because this working branch is governed by
  PR-only and required-workflow rules. No merge, approval, admin, or force-push
  bypass was used. Remote PR #2 required Checks, qualifying approvals, and a
  protected merge SHA remain outstanding.

## Ordered development loop

1. Wait for required Checks and independent review on the current exact PR #2
   head; the production candidate is `a8fb3a6` and later branch commits are
   docs-only baseline refreshes. Do not rerun unchanged checks or treat queued
   status as success.
   The organization’s central hourly review scheduler is configured for
   `27 * * * *`; it is not duplicated in this repository.
2. Merge #2 through the protected path only after two qualifying approvals,
   last-push approval, resolved threads, and terminal required Checks; verify
   the merge SHA. Then re-sync PR #4 normally to the integrated `develop`
   foundation and process its protected gate before stacking #7.
3. Deliver #7's immutable statement registry, then #8's deterministic match
   and bridge, then close the parent #6 product slice.
4. Deliver #9 as a separate authorization boundary so tenant identity and
   accounting authority remain distinct.
5. Release only after runtime deployment, recovery, security, and buyer
   workflow evidence is current; then update the version and `CHANGELOG.md`.

## Authority and standards traceability

The detailed product, technical, security, data, operating, ADR, and APA 7th
reference records remain authoritative in `docs/PRD.md`, `docs/TRD.md`,
`docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/DATA_MODEL.md`,
`docs/OPERABILITY.md`, `docs/adr/`, and `docs/doctoring/`. This baseline adds
no unsupported accounting, tax, identity, or model-generated facts.
