# Stacked reconciliation snapshot authority repair — 2026-09-02

## Problem statement

The reconciliation lifecycle parent and maker-checker exception-resolution child each strengthened database authority independently, but their original trigger names composed unsafely once the child was restacked onto the changed parent. Parent migration overlay `0019_reconciliation_run_database_snapshot_authority.sql` introduced `accounting_reconciliation_transition_database_authority_guard`, which derives the exact statement/book bridge and overwrites caller-selected snapshot, statement-population, and book-population identities. The child migration 0021 still carried an older `accounting_reconciliation_transition_authority_snapshot_guard` that derived a snapshot containing resolution-command state but ran lexically before the new parent trigger.

PostgreSQL executes triggers of the same kind for the same event in alphabetical name order. On the combined stack the child `...authority_snapshot_guard` therefore ran first and the parent `...database_authority_guard` ran afterward, overwriting the child resolution-aware snapshot before the transition-command hash guard. The final row retained the stronger parent source bridge but could lose the child maker-checker resolution-command population from the persisted lifecycle snapshot. This was a stacked authority regression, not a merge-conflict formatting issue.

## Test-first trace

`tests/test_reconciliation_resolution_snapshot_overlay_contract.py` was added first. It requires the final trigger order to be parent database authority, then child resolution evidence, then transition command hash; rejects the obsolete child trigger name; and requires the child overlay to bind the parent snapshot and both population references together with immutable exception-resolution command hashes, evidence hashes, and terminal decisions.

The real PostgreSQL regression `tests/test_reconciliation_lifecycle_database_authority_postgres.py` was also strengthened so a privileged direct INSERT supplies three syntactically valid forged identities and must observe PostgreSQL replace all three. The untied-bridge case must still fail through the parent `reconciliation_database_bridge_unexplained` invariant, proving the child overlay did not weaken the parent monetary authority.

## Production repair

Migration `0021_reconciliation_exception_resolution_outbox_pair.sql` no longer reimplements the complete statement/book bridge. It owns only the child concern in addition to its deferred resolution-command/status/outbox invariant.

`accounting_core.assign_reconciliation_run_resolution_snapshot()` calls the parent `accounting_core.reconciliation_run_database_snapshot_authority(tenant_id, run_id)` to obtain the server-owned base snapshot and both population references. It then reads the immutable ordered `reconciliation_exception_resolution_command` population for the same tenant/run and hashes a domain-separated payload containing:

- the parent database snapshot hash;
- the parent statement-population reference;
- the parent book-population reference; and
- resolution command identity, exception identity, idempotency key, target terminal status, retained evidence identity/reference/hash, source-payload hash, database-owned resolution-command hash, reviewer actor, purpose, effective time, and recorded time.

The child trigger is now `accounting_reconciliation_transition_evidence_snapshot_guard`. The exact lexical order is therefore:

1. `accounting_reconciliation_transition_database_authority_guard` — parent bridge and all three caller-substitution defenses;
2. `accounting_reconciliation_transition_evidence_snapshot_guard` — child maker-checker resolution evidence composition while preserving parent population references; and
3. `accounting_reconciliation_transition_hash_guard` — immutable transition-command hash over the final database-owned snapshot and parent population identities.

This is monotonic authority composition: the child adds its bounded-context evidence without duplicating or weakening the parent accounting bridge.

## DDD and ownership

Both slices remain in the **Reconciliation Review** supporting subdomain and the existing `reconciliation_run` aggregate boundary. The parent owns source/population/bridge authority for lifecycle finalization. The child owns the named exception-resolution command and the maker-checker evidence that must be inseparable from a terminal reconciliation snapshot. The minimal shared kernel remains tenant/run identity plus the existing reconciliation idempotency and lifecycle-lock contracts.

No new journal-posting, journal-reversal, fiscal-period-close, chart-account-selection, or accounting-policy authority is created. Billing, payments, settlement, identity, orchestration, Context Fabric, and EA remain foreign/read-only boundaries for this accounting-control decision.

## Migration and operability boundary

The stack is unreleased. The supported installer must apply the base foundation chain, then the parent `0019_reconciliation_run_database_snapshot_authority.sql` overlay, then migrations 0020 and 0021. This ordering is explicit because the child overlay calls the parent authority function. Once any of these migrations reaches a protected/released source, later corrections must use forward migrations rather than editing applied history.

The repair does not transfer evidence across heads. The combined child head must still execute the real PostgreSQL suite, exact 100% owned statement/branch and edge-case coverage, repository/public-docstring contracts, security/SAST/dependency gates, reproducible package/SBOM/provenance, and current-head independent review before it can integrate into the parent.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Binary string functions and operators*. https://www.postgresql.org/docs/18/functions-binarystring.html
