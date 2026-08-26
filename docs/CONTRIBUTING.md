# Contributor and agent operations

Buyer and operator documentation lives in the root [README](../README.md). This file keeps repository-operation rules that are not product claims.

## Writer boundary

- This repository is the only CWL authority for legal books, posted journals, reversals, fiscal-period control, trial balances, bank-reconciliation accounting evidence, and `accounting_posting_receipt`.
- The Metering Billing Platform owns commercial facts and `accounting_journal_proposal`. Billing and other sources may propose semantic account roles. They may not select final chart-account identifiers, write journal tables, or claim that a proposal has posted.
- AI systems may explain or propose classifications. They cannot approve policy, open periods, map chart accounts, post journals, or approve reconciliations.
- Repositories with their own enabled dedicated writers are read-only dependencies from this lane. Integrate only through published package/API/event contracts and existing owner-control paths.

Normative development rules are in [AGENTS.md](../AGENTS.md) and [CLAUDE.md](../CLAUDE.md).

## Independent verification

Run the commands in the root README from this checkout only. Do not require a Naruon or sibling worktree. Production statement and branch coverage must remain 100%. `scripts/validate_repository.py` rejects missing required files, unresolved placeholder tokens, mutable GitHub Action tags, invalid SQL naming, and destructive mutation of append-only journal tables.

Real PostgreSQL integration is required for database-owned invariants. Local reproduction is supporting evidence only; merge readiness requires the applicable GitHub jobs to execute against the unchanged exact head.

## Exact-head evidence

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) checks out the exact commit under test, pins Actions to full commit SHAs, and installs quality dependencies with `--require-hashes`. Repository-owned SAST, secret scanning, dependency-vulnerability review, coverage, package reproducibility, SBOM, and provenance evidence must be tied to the same exact head and independently resolved live base where applicable.

Queued, pending, skipped, cancelled, absent, neutral, failed, stale, predecessor, status-only, synthetic-merge-only, or model-only evidence is non-passing. A green predecessor head does not transfer evidence to a new head. Push-only integrated attestations are release evidence only after the exact protected integrated head actually runs them successfully.

## Pull requests and the live ruleset

The protected integration branch is `develop`. Before any Ready or merge transition, refetch the live ruleset, exact base tip, current head, formal reviews, unresolved threads, and required workflows; remembered settings are stale. Satisfy the currently enforced approval count, stale-review dismissal behavior, review-thread resolution, required workflows, and merge-method constraints without using administrator bypass as the normal path.

Process dependency roots before successors. A successor authored on a predecessor branch must be rebuilt and revalidated against the exact integrated protected base after the predecessor merges. Preserve only its unique product or documentation intent; do not carry stale implementation claims or treat predecessor evidence as current-head evidence. Never force-push or destructively rebase merely to make a stacked PR look current.

The durable implementation order is maintained in [`docs/doctoring/IMPLEMENTATION_SEQUENCE.md`](doctoring/IMPLEMENTATION_SEQUENCE.md). The immutable ISO 20022 bank-statement evidence registry is already integrated. Deterministic reconciliation and the exact book-to-bank bridge are the next buyer-visible accounting dependency; statement evidence never posts a journal automatically.

## Review evidence is not the product

CodeRabbit, Devin, OpenCode, Strix, Noema, and similar summaries are review artifacts. They are not the buyer/operator story, the published accounting contract, or proof of readiness. Verify each finding against the current source. Resolve only addressed current-head threads, never self-approve, and never manufacture missing approval or scan evidence.

## When behavior or material documentation changes

Update `CHANGELOG.md`, the relevant ADR, and [STANDARD_TRACEABILITY.md](doctoring/STANDARD_TRACEABILITY.md) when authority, contracts, accounting policy boundaries, material standards decisions, or monetary invariants change. Keep APA 7 primary-source references in [REFERENCES.md](doctoring/REFERENCES.md). Write a failing test before changing reference-core or durable accounting behavior.
