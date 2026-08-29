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
and migration indexes. Required named constraints must resolve on the expected
relation with their canonical PostgreSQL-18 type and rendered definition and
must remain validated and enforced. Required explicit indexes must resolve to
an actual `pg_index` entry that is valid, ready, and live and whose canonical
PostgreSQL-18 `pg_get_indexdef()` fingerprint preserves the checked-in relation,
keys/expressions, uniqueness, and predicate semantics. Readiness additionally
binds each checked-in behavior-defining accounting trigger to its exact table,
trigger name, zero-argument function and canonical stored-function definition
fingerprint, enabled state, row/event mask, unrestricted `WHEN` predicate, and
canonical `UPDATE OF` column contract. The two deferred journal-balance
constraint triggers retain the stricter constraint/deferrable checks and the
canonical stored `assert_journal_balance()` definition fingerprint. The probe
requires an active binding even for privileged sessions and caps connection
establishment at five seconds while preserving a stricter configured timeout.
Return `200` with `{"status":"ready"}` only after all checks pass. Return `503`
with stable operator guidance when a check fails; do not return driver,
connection, or database-object details to the caller. Both readiness responses
carry `Cache-Control: no-store` so an intermediary cannot reuse a stale
readiness result.

The probes are operational signals only. They do not authenticate callers,
authorize accounting commands, or replace exact-head CI, security, package,
recovery, and independent-review evidence for release.

## Consequences

Deployment systems can remove an instance from service when its accounting
database or tenant provisioning is unavailable, while process supervision can
continue to use the cheaper liveness probe. Readiness checks add one bounded
database session per probe and should therefore be polled at an operationally
reasonable interval. The endpoint does not apply migrations or repair tenant
provisioning. A missing, disabled, detached, conditionally narrowed, or
column-scope-drifted accounting-control trigger, a weakened same-name
constraint, or a redefined same-name migration index is treated as schema drift
and fails readiness closed rather than silently reducing database-owned
accounting enforcement.

## Evidence

The initial readiness regression was written before implementation: the
PostgreSQL HTTP integration test expected `GET /readyz` to return `200` and
observed `404` on the unchanged foundation. A second regression verifies that
an unreachable database returns `503` with operator-safe guidance and no raw
driver or connection error. A third regression verifies that disabling either
required journal-balance trigger returns `503` for a bound runtime login. A
fourth regression recreates each balance trigger with a `WHEN (false)` predicate
and, where PostgreSQL permits it, an `UPDATE OF` column restriction; each
altered definition returns `503` and the canonical migration definition is
restored after the check. A fifth regression replaces
`assert_journal_balance()` in place with the same signature, proves the no-op
replacement removes database balance enforcement, verifies readiness fails
closed, and then restores and re-proves the canonical balance guard. The
behavior-trigger contract regression then disables each checked-in ordinary
accounting control trigger—including period, finalized-ledger, period-open,
soft-close evidence, bank-statement evidence, and reconciliation-scope guards—
and requires readiness to fail closed. It separately replaces the soft-close
`UPDATE OF` registration with a broader `UPDATE` trigger and requires the same
fail-closed result before restoring the checked-in definition. Definition-drift
regressions additionally replace a required constraint with a weaker same-name
`CHECK (true) NOT VALID` control and replace a required index with a same-name
constant-expression index; both must fail readiness, and cleanup restores the
canonical schema even when replacement setup itself fails. Static repository
contracts keep the constraint/index fingerprint maps aligned with their
readiness inventories.
