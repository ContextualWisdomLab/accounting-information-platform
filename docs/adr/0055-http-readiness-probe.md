# ADR 0055: Separate HTTP liveness and database readiness

- Status: Proposed
- Date: 2026-08-29

## Context

The host-mountable HTTP surface already provides `GET /healthz`, but a process
that can answer HTTP may still have an unavailable PostgreSQL database, an
unbound runtime tenant, or an incomplete accounting migration. Treating
liveness as accounting readiness can route traffic to a process that cannot
serve the authoritative accounting boundary.

## Decision

Keep `GET /healthz` as a database-independent liveness response. Add
`GET /readyz`, which opens the configured PostgreSQL 18 session, verifies the
database-controlled runtime tenant binding, and checks the complete current
schema contract through migration `0014_reconciliation_candidate_allocation.sql`.
Because the repository has no durable schema-version table, this contract is
represented by the current tables, functions, mutated columns, constraints,
and migration indexes, plus the enabled exact registrations of every
database-owned guard trigger through migration `0013`, including the two
deferred journal-balance triggers, their unrestricted event definitions, and
the exact `UPDATE OF` column sets for scope/evidence guards. The probe requires an active
binding even for privileged sessions and caps connection establishment at five seconds while
preserving a stricter configured timeout. Return `200` with
`{"status":"ready"}` only after all checks pass. Return `503` with stable
operator guidance when a check fails; do not return driver, connection, or
database-object details to the caller. Both readiness responses carry
`Cache-Control: no-store` so an intermediary cannot reuse a stale readiness result.

The probes are operational signals only. They do not authenticate callers,
authorize accounting commands, or replace exact-head CI, security, package,
recovery, and independent-review evidence for release.

## Consequences

Deployment systems can remove an instance from service when its accounting
database or tenant provisioning is unavailable, while process supervision can
continue to use the cheaper liveness probe. Readiness checks add one bounded
database session per probe and should therefore be polled at an operationally
reasonable interval. The endpoint does not apply migrations or repair tenant
provisioning.

## Evidence

The initial readiness regression was written before implementation: the
PostgreSQL HTTP integration test expected `GET /readyz` to return `200` and
observed `404` on the unchanged foundation. A second regression verifies that
an unreachable database returns `503` with operator-safe guidance and no raw
driver or connection error. A third regression verifies that disabling either
required journal-balance trigger returns `503` for a bound runtime login. A
fourth regression recreates each trigger with a `WHEN (false)` predicate and
an `UPDATE OF` column filter in turn; each altered definition returns `503` and
the canonical migration definition is restored after the check. A fifth
regression replaces `assert_journal_balance()` in place with the same signature;
the readiness probe rejects the changed function definition even though the
trigger OID and relation/event registration remain unchanged.
The non-balance trigger contract is also exercised by disabling close and
immutable-fact controls; each disabled registration returns `503`.
