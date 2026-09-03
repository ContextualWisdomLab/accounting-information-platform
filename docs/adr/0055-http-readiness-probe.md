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
database-controlled runtime tenant binding, and checks the required current
schema contract through migration `0015_reconciliation_policy_repair.sql`.
Because the repository has no durable schema-version table, this contract is
represented by the current tables, functions, mutated columns, constraints,
and migration indexes. Readiness additionally binds each checked-in
behavior-defining accounting trigger to its exact table, trigger name,
zero-argument function and canonical stored-function definition fingerprint,
enabled state, row/event mask, unrestricted `WHEN` predicate, and canonical
`UPDATE OF` column contract. The two deferred
journal-balance constraint triggers retain the stricter constraint/deferrable
checks and the canonical stored `assert_journal_balance()` definition
fingerprint. Required constraints are also bound to their relation, type,
validated/enforced and non-deferrable state, and canonical
`pg_get_constraintdef()` fingerprint. Required indexes are bound to their
owning relation, valid/ready and uniqueness state, predicate, and canonical
`pg_get_indexdef()` fingerprint. Required column metadata is compared as an
ordered canonical prefix, allowing compatible additive tables and columns
without allowing a missing or altered required column. The probe also requires forced row-level
security on every tenant-scoped fact table, the exact public tenant-isolation
policy on each such table, and the canonical `current_tenant_account_id()`
definition fingerprint. It requires an active binding even for privileged
sessions, caps connection establishment at five seconds, installs the
five-second statement timeout in startup options before the first connected
command, preserves a stricter configured timeout, and applies one remaining
five-second total budget across the connected operation. Readiness rejects
multi-host connection strings because per-host connection timeouts could
otherwise multiply that budget. Return `200` with
`{"status":"ready"}` only after all checks pass. Return `503` with stable
operator guidance when a check fails; do not return driver, connection, or
database-object details to the caller. Both readiness responses carry
`Cache-Control: no-store` so an intermediary cannot reuse a stale readiness
result.

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
column-scope-drifted accounting-control trigger is treated as schema drift and
fails readiness closed rather than silently reducing database-owned accounting
enforcement. A same-name weakened constraint or index, disabled forced RLS,
missing or broadened tenant policy, or changed tenant-binding function is
likewise schema drift. A blocked or slow connected query fails closed at the
statement timeout instead of retaining an HTTP worker indefinitely. Additive
schema objects remain compatible with the probe because only the recorded
canonical column prefix is required.

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
fail-closed result before restoring the checked-in definition.
The definition-contract regression replaces every required constraint with a
same-name `CHECK (true)` and every required index with a same-name single-column
non-unique index; each altered catalog object returns `503` before its canonical
definition is restored. A sixth regression inventories every tenant-scoped
table's forced RLS and exact public tenant policy, drops RLS or a policy and
requires readiness to fail closed, and holds a connected tenant lookup behind a
slow function to prove the HTTP response is bounded by the startup-installed
statement timeout. A separate regression adds a table and column and proves
compatible additive schema remains ready; migration inventory parser tests
cover conditional declaration modifiers. An upgrade regression removes the
four policies from an installed `0014` state, applies only `0015`, and proves
restricted-runtime readiness recovers.
