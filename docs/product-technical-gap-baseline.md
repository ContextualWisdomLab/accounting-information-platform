# Product and technical gap baseline

**Evidence date:** 2026-08-20 (Asia/Seoul)

This baseline separates observed repository and GitHub state from proposed
work. It is the working queue for the buyer-visible product loop; it does not
claim that a local change is merged or that a queued remote check is live
success.

## Observed current state

- The protected default branch is `develop`; the repository was bootstrapped
  from an empty tree and the accounting foundation is currently developed on
  PR #2 (`feat: establish accounting posting foundation`).
- PR #2 is now Ready for review at its exact current head. It remains
  `MERGEABLE` but `BLOCKED`, with `REVIEW_REQUIRED` and required Checks queued;
  the head and gate state are re-fetched before every synchronized push. Its
  temporary repair lane has been removed after the contract repairs and was not
  itself product evidence.
- PR #4 is a Draft documentation stack on top of the foundation branch. PR #5
  is merged documentation for the buyer/operator README.
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
- Real PostgreSQL 18.4 on `127.0.0.1` was used with a local test role and
  database. The full suite passed: 182 tests, including HTTP and PostgreSQL
  integration coverage.
- Branch and statement coverage passed at 100%: 3,452 statements, 1,262
  branches, zero misses or partial branches.
- The current synchronized tree's CI-equivalent local run also passed: 217
  tests, 3,554 statements, 1,312 branches, zero misses or partial branches,
  repository contracts, and compilation. This remains local evidence, not
  remote Checks evidence.
- `scripts/validate_repository.py`, `compileall`, and `git diff --check`
  passed locally. These are local observations, not remote Checks results.

## Ordered development loop

1. Push the smallest current-head foundation fixes, re-fetch the exact PR
   head, and wait for required Checks/review without treating the wait as a
   blocker. The organization’s central hourly review scheduler is configured
   for `27 * * * *`; it is not duplicated in this repository.
2. Remove the temporary repair workflow only after its purpose is fulfilled
   and the integrated branch retains the verified database authority tests.
3. Merge #2 through the protected path, verify the merge SHA, then stack #7.
4. Deliver #7's immutable statement registry, then #8's deterministic match
   and bridge, then close the parent #6 product slice.
5. Deliver #9 as a separate authorization boundary so tenant identity and
   accounting authority remain distinct.
6. Release only after runtime deployment, recovery, security, and buyer
   workflow evidence is current; then update the version and `CHANGELOG.md`.

## Authority and standards traceability

The detailed product, technical, security, data, operating, ADR, and APA 7th
reference records remain authoritative in `docs/PRD.md`, `docs/TRD.md`,
`docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/DATA_MODEL.md`,
`docs/OPERABILITY.md`, `docs/adr/`, and `docs/doctoring/`. This baseline adds
no unsupported accounting, tax, identity, or model-generated facts.
