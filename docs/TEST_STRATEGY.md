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
8. **Read models** — trial balance, journals, ledgers, balances, rollforwards, aging, statements, close package, VAT register and audit/outbox views tie to the authoritative population.
9. **Integration contracts** — Billing proposal GET/pull envelopes, idempotent retry, origin allowlist and fail-closed remote errors.
10. **Security / abuse** — cross-tenant references, malformed origins, oversized bodies, hostile parser inputs, privilege escalation and replay storms.
11. **Packaging / release** — clean install, wheel install, typing marker, migration install / upgrade, SBOM / provenance and recovery rehearsals.

## PostgreSQL accounting regressions

Use PostgreSQL 18 integration tests for controls that exist at the storage or role boundary.

Required regressions include:

- direct one-sided journal commit fails through the deferred balance trigger;
- direct line-less journal commit fails and rollback leaves no journal row;
- balanced application posting still commits normally;
- direct update/delete of finalized journal, line, source-reference, reversal, receipt and proposal-source facts fails at the database boundary;
- after an authoritative posting receipt exists, a late journal-line or source-reference insert into that finalized journal fails before it can extend the monetary/evidence population;
- hard-closed periods reject later inserts;
- a login that merely sets `accounting_core.journal_write_role` cannot insert into a soft-closed period;
- a purpose-limited session login that is a member of `accounting_closing_writer` can exercise the supported soft-close exception path;
- migration `0005` changes a pre-existing LOGIN `accounting_closing_writer` back to `NOLOGIN`;
- every tenant-scoped authoritative table both enables and forces RLS;
- a non-owner, non-superuser, non-`BYPASSRLS` runtime login can execute an ordinary supported posting/read path for its bound tenant but cannot read another tenant or acquire owner / administrative authority.

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

## Merge gates

One unchanged exact PR head must pass all applicable gates together:

- production statement coverage: **100%**;
- production branch coverage: **100%**;
- public production API docstrings: complete;
- all behavior and real PostgreSQL integration tests;
- repository contracts and compile checks;
- hash-locked package build and installed-public-API smoke test;
- SAST and security scans;
- dependency / package policy, SBOM and provenance checks where configured;
- migration clean-install / upgrade rehearsal;
- qualifying independent approval and zero still-valid unresolved review findings.

Queued, pending, skipped, cancelled, absent, neutral, failed, stale, predecessor-head, synthetic merge-ref, status-only or model-only evidence is non-passing.

## Release / recovery acceptance

Before a release tag, run backup / restore and rollback-strategy rehearsal with production-like PostgreSQL state and the restricted runtime identity. Verify that restored journal, receipt, close and outbox hashes correspond to the exact release source and artifacts. A successful CI helper workflow is not itself release evidence if generated changes were not normalized into the canonical source branch.
