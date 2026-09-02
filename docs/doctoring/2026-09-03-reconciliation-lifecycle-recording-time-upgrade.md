# Reconciliation lifecycle recording-time upgrade authority

## Problem

Migration 0019 created immutable `reconciliation_run_transition_command` rows with a `recorded_at` default but did not prevent a privileged caller from supplying that column explicitly. Migration 0025 later made new lifecycle transition recording time PostgreSQL-owned. Without an upgrade boundary, an older transition with caller-shaped system time could survive into the stronger schema and continue to support a `reconciled` run even though the database cannot prove the origin of its historical timestamp.

The defect is materially different from ordinary historical metadata. A lifecycle transition is accounting-control evidence used to establish a reconciled run. Silently treating an unverifiable pre-0025 timestamp as database-clock provenance would manufacture evidence rather than repair it.

## Constraints and alternatives

The transition row is already immutable and paired with status/outbox evidence. Rewriting its historical `recorded_at`, deleting it, or synthesizing a replacement command would destroy or invent audit provenance. A `legacy_unverified` marker alone is also insufficient unless every downstream authority read is changed to reject it.

The selected bounded repair is therefore fail-closed upgrade admission. Before adding the new recording-time authority column, migration 0025 receives transaction-scoped `SELECT` visibility for the forced-RLS transition table, refuses installation if any pre-0025 transition command exists, and removes the temporary policy before durable schema change. Operators must keep the older release or perform a separately reviewed audited remediation backed by the original transition, status, and outbox evidence. A new run does not erase an old immutable transition row and therefore cannot satisfy this preflight by itself.

For databases that pass the preflight, `recording_time_authority_code` is explicit in the relational model, new transition inserts overwrite `recorded_at` with `clock_timestamp()` and set `database_clock`, and `effective_at > recorded_at` is rejected. No posting, reversal, fiscal-period close, or accounting-policy authority is added.

## Why this boundary

PostgreSQL documents that `clock_timestamp()` returns the actual current time and can change within one statement, unlike `CURRENT_TIMESTAMP`/`transaction_timestamp()`, which are transaction-start time. That makes it appropriate for the system-recording instant assigned by the row trigger. PostgreSQL also documents that a forced-RLS table owner is subject to row security and that multiple permissive policies combine with OR semantics. The temporary current-user SELECT policy is therefore required for an all-tenant migration preflight and is deliberately scoped to the migration transaction.

Multiple same-event triggers fire in alphabetical order. The recording-time trigger currently runs after the transition hash trigger; this is acceptable because the existing transition command hash does not claim to include `recorded_at`. If a later contract binds system time into that hash, trigger ordering and hash-version semantics must change together under a new RED/GREEN migration.

## Acceptance evidence

`tests/test_reconciliation_lifecycle_recording_time_upgrade_contract.py` requires the forced-RLS-safe preflight to precede durable authority changes and requires post-upgrade database-clock assignment. `tests/test_reconciliation_lifecycle_recording_time_upgrade_postgres.py` builds a pre-0025 database under a non-`BYPASSRLS` migration owner, inserts controlled legacy transition evidence with caller-shaped recording time, and requires migration 0025 to abort without leaving the temporary policy or authority column behind. The existing future-effective PostgreSQL regressions remain responsible for proving new transitions cannot use caller-supplied system time to make a future decision current.

The complete candidate remains non-passing until these tests, the real PostgreSQL suite, exact owned statement/branch coverage, public-docstring/repository contracts, security/SAST/dependency evidence, reproducible package/SBOM/provenance, and current-head review all pass on one unchanged head.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Date/time functions and operators*. https://www.postgresql.org/docs/18/functions-datetime.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE POLICY*. https://www.postgresql.org/docs/18/sql-createpolicy.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
