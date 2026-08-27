# ADR 0055: Reconciliation approval terminal-state authority

- Status: Proposed
- Date: 2026-08-28

## Context

A durable reconciliation decision is an accounting-control fact, not a caller convention. Migration `0016_reconciliation_approval_evidence.sql` stores one immutable approval decision per tenant, reconciliation run, and match. The decision domain is `approved` or `rejected`; neither decision grants journal posting, reversal, fiscal-period close, or accounting-policy authority.

The initial approval guard protected only entry into `approved`. Real PostgreSQL RED tests demonstrated that a caller could move a proposed match directly to `rejected` without a durable rejected decision and could reopen reviewed `approved` or `rejected` matches to `proposed`. That made the stored decision evidence weaker than the match state it was meant to authorize.

## Decision

PostgreSQL owns the reviewed reconciliation-match transition graph.

- `proposed -> approved` requires one immutable `reconciliation_approval` row for the same tenant/run/match whose `approval_decision_code` is `approved`, and the match must carry `approved_at`.
- `proposed -> rejected` requires one immutable row for the same tenant/run/match whose decision is `rejected`; a rejected match cannot carry `approved_at`.
- A reviewed `approved` or `rejected` match cannot reopen to `proposed` or switch directly to the opposite decision.
- A reviewed terminal match may move to `superseded`. Supersession retires active use of the reviewed match without rewriting or deleting its immutable approval fact.
- Repeating the same reviewed status may not mutate `approved_at`; corrections use a new reviewed match/control fact rather than rewriting reviewed evidence.

The approval row remains append-only and unique per tenant/run/match. The match-state guard is database-owned so privileged SQL cannot bypass the control through application-layer conventions.

## Consequences

Audit and close-review projections can distinguish proposed work from an explicitly approved or rejected human control fact. Rejected decisions are no longer status-only assertions, and reviewed outcomes cannot be silently reopened. Supersession remains the explicit retirement path while historical approval evidence is retained.

This ADR does not make reconciliation approval an accounting posting command. Any adjustment journal still enters the existing accounting command boundary with its own authorization, immutable source evidence, period admission, exact-decimal validation, idempotency identity, and authoritative posting receipt.

## Verification

Exact RED head `a654d3d60d999eba354e7bcf703f61dfddc298cb` ran 485 PostgreSQL-backed tests in Accounting Foundation CI `33116225624`. Three product regressions failed exactly at the missing state-machine boundary: status-only rejection, `approved -> proposed`, and `rejected -> proposed`. A fourth error came from a duplicated test fixture identity in the combined supersession case; that fixture defect was split into independent approved/rejected supersession tests before the production repair.

The narrow GREEN changes only migration 0016's database guard: decision-code-consistent terminal entry, terminal-state non-reopening, and explicit supersession. Exact-head evidence must be regenerated after every subsequent commit; predecessor results do not transfer.
