# Book-period control insert-authority traceability

## Finding

`accounting_book_period_control` is the authoritative tenant/accounting-book/fiscal-period close-state intersection. Migration 0034 already limits automatic post-install materialization to open periods and deliberately leaves missing non-open pairs absent. Fresh application review found a competing writer in `PostgresPostingLedger._lock_book_period()`: before locking the requested control row, it attempted `INSERT ... SELECT` from the shared `fiscal_period` row and copied `fiscal_period.period_status_code` and `period_closed_at` into book-scoped authority.

That path could synthesize `soft_closed` or `hard_closed` authority for a later-created book without that book's close command, maker-checker evidence, retained trial-balance snapshot, close event, or chronology. It also selected every active book for the tenant rather than only the requested book. The resulting state contradicted migration 0034's open-only master-data lifecycle and the domain model's single-writer boundary.

This is a DDD/data-authority defect, not an IFRS interpretation. IFRS does not prescribe PostgreSQL trigger nesting or book-period control rows.

## Selected control

Migration `0034_book_period_control_seed.sql` now makes the database relation itself reject direct post-install control creation. The one-time migration repair runs first. After that repair, `book_period_control_insert_authority_guard` is installed as a row-level `BEFORE INSERT` trigger on `accounting_book_period_control`.

The guard admits a new row only when both conditions hold:

- the control INSERT is nested under one of migration 0034's canonical master-data seed triggers (`pg_trigger_depth() >= 2`); and
- the new control is literal `open` with `period_closed_at IS NULL`.

A direct runtime/application INSERT therefore returns no row. `_lock_book_period()` immediately performs its authoritative control lookup; when the requested pair is legitimately absent, the existing domain validation path reports that the accounting book has no control row and requires control-data repair. No shared `fiscal_period` status becomes book close authority.

A caller-controlled custom GUC was rejected as an authority marker because an arbitrary session setting would itself become a spoofable mutable capability. PostgreSQL's trigger-depth signal is structural: PostgreSQL 18.6 documents `pg_trigger_depth()` as the current trigger nesting level, returning zero outside trigger execution. PostgreSQL also documents that a row-level `BEFORE` trigger can skip the current row operation, which is the fail-closed mechanism used here.

The guard function remains `SECURITY DEFINER`, fixes its `search_path` to `pg_catalog, pg_temp`, and revokes PUBLIC execute. This does not grant accounting authority to Billing or another foreign context and does not alter posted facts, financial amounts, period transitions, retained snapshots, or reconciliation evidence.

## TDD and implementation evidence

Real-PostgreSQL RED `614d1164f3abf1f7bab3fe77d520e5b7108e4c69` creates a tenant period whose shared compatibility projection is `soft_closed`, inserts a later active accounting book, confirms that migration 0034 creates no control/fence for that non-open pair, then calls `_lock_book_period()`. The acceptance requires the call to fail with the existing `AccountingValidationError` and requires both control and fence populations to remain absent.

Production candidate `610d77082eb01c80d2e9e74521e48a3b06e1375a` installs the post-repair direct-insert guard in migration 0034. Static ratchet `ee233b5c40c942008c7ec034917fd49f1fcf9976` pins the structural nesting check, open/NULL-only invariant, `BEFORE INSERT` placement, and migration-repair ordering.

Application-source RED `tests/test_book_period_application_authority_contract.py` separately requires the persistence adapter to remove the stale `INSERT ... SELECT` writer, replace both shared-state `LEFT JOIN`/`COALESCE` fallbacks with an exact `accounting_book_period_control` join, and lock the authoritative control row with `FOR UPDATE OF accounting_book_period_control`. Real-PostgreSQL `tests/test_postgres_book_period_control_no_projection_red.py` supplies the buyer-relevant missing-non-open-control case. Database containment is therefore not treated as permission to leave the application authority model permanently divergent.

These SHAs are development lineage, not release evidence. The RED was authored before the causal repair, but it was not observed failing on a GitHub runner in this run. Exact-head real-PostgreSQL execution, security/SAST/dependency evidence, independent review, protected-stack prerequisites, migration/recovery evidence, and immutable release evidence remain separate gates.

## Scope-preservation repair

A source-cleanup candidate at `952bb1b2a014db823f8ee452ebdfb9bc3980e733` attempted to replace the three stale helper paths together. Exact-blob verification immediately found that the replacement did not preserve unrelated methods in the large persistence adapter, so that candidate is invalid development evidence and must not be used as a GREEN or release input.

Normal descendant `a8c5abe7520cb0a50708127726bcf0dfb420dc60` restores `src/accounting_information_platform/persistence.py` byte-for-byte to prior complete blob `1d27c2399b0adca1aead3a2f3a141a8eb6a95435`. No force-push, reset, destructive rebase, or selective loss of concurrent delta was used. The application-source REDs therefore remain intentionally RED until a scope-preserving causal edit changes only the three authority helpers on a freshly read exact head.

This recovery is part of the verification record: changing an authority boundary is not acceptable if the patch silently deletes unrelated reporting, reconciliation, integration, or audit behavior. The next implementation must prove both the authority assertions and preservation of the rest of the persistence module before it can be called GREEN.

## Recovery and follow-up

A rejected direct control INSERT writes no authoritative row and therefore seeds no 64-row journal-population fence. The surrounding close transaction remains free to roll back normally. Operators must not repair a missing non-open pair by copying `fiscal_period` status or by manual SQL. If a later-created book must become applicable to an already non-open period, that requires an explicit book-period lifecycle/adoption/reopen command with authenticated capability, idempotency, maker-checker policy where applicable, and retained chronology.

The stale application-side `INSERT ... SELECT` is now behaviorally contained by the database single-writer boundary, but its source expression remains a cleanup finding: it should be removed from `_lock_book_period()` on a current exact head, leaving the helper as read/lock/fail-closed only. `_require_open_book_period_bounds()` and `_load_book_period_state()` must likewise read book-owned status only from `accounting_book_period_control`; a missing control must fail closed rather than inherit the shared calendar projection. Until those REDs are satisfied, the database guard is authoritative and the branch must not claim the application source is conceptually clean.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18.6 documentation: System information functions and operators*. https://www.postgresql.org/docs/18/functions-info.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18.6 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18.6 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
