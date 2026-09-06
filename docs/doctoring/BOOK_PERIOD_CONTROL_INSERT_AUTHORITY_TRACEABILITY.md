# Book-period control insert-authority traceability

## Finding

`accounting_book_period_control` is the authoritative tenant/accounting-book/fiscal-period close-state intersection. Migration 0034 already limits automatic post-install materialization to open periods and deliberately leaves missing non-open pairs absent. Fresh application review found a competing writer in `PostgresPostingLedger._lock_book_period()`: before locking the requested control row, it attempted `INSERT ... SELECT` from the shared `fiscal_period` row and copied `fiscal_period.period_status_code` and `period_closed_at` into book-scoped authority.

That path could synthesize `soft_closed` or `hard_closed` authority for a later-created book without that book's close command, maker-checker evidence, retained trial-balance snapshot, close event, or chronology. It also selected every active book for the tenant rather than only the requested book. The resulting state contradicted migration 0034's open-only master-data lifecycle and the domain model's single-writer boundary.

The same authority leak remained on the read side: `_load_book_period_state()` and `_require_open_book_period_bounds()` used an outer join plus `COALESCE` to inherit the shared `fiscal_period.period_status_code` whenever the book-owned control was absent. Database containment therefore prevented a bad control write but did not make the application authority model conceptually correct.

A later review found a separate observability defect in the database containment itself. The direct-write branch of `guard_book_period_control_insert_authority()` returned `NULL`. PostgreSQL correctly skipped the row, but a raw SQL or application caller could observe a non-error command completion and mistake the attempt for accepted authority unless it separately checked affected-row count or re-read the control. An authoritative close-state write must fail explicitly at the boundary that rejects it; silent no-op semantics are not acceptable evidence.

This is a DDD/data-authority defect, not an IFRS interpretation. IFRS does not prescribe PostgreSQL trigger nesting or book-period control rows.

## Selected control

Migration `0034_book_period_control_seed.sql` makes the database relation itself reject direct post-install control creation. The one-time migration repair runs first. After that repair, `book_period_control_insert_authority_guard` is installed as a row-level `BEFORE INSERT` trigger on `accounting_book_period_control`.

The guard admits a new row only when both conditions hold:

- the control INSERT is nested under one of migration 0034's canonical master-data seed triggers (`pg_trigger_depth() >= 2`); and
- the new control is literal `open` with `period_closed_at IS NULL`.

A direct runtime/application INSERT now raises PostgreSQL `check_violation` with stable marker `book_period_control_insert_authority_required`; it does not return a silently skipped command as if authority had been accepted. The application matches that database single-writer boundary instead of relying on the guard as a compensating control. `_lock_book_period()` performs a diagnostic fiscal-period existence lookup, then reads and locks only the requested `accounting_book_period_control` row with `FOR UPDATE OF accounting_book_period_control`; it no longer creates controls. `_load_book_period_state()` and `_require_open_book_period_bounds()` use an inner join to the same book-owned control and read its `period_status_code` directly. A missing control therefore fails closed instead of inheriting the shared calendar projection.

A caller-controlled custom GUC was rejected as an authority marker because an arbitrary session setting would itself become a spoofable mutable capability. PostgreSQL's trigger-depth signal is structural: PostgreSQL 18.6 documents `pg_trigger_depth()` as the current trigger nesting level, returning zero outside trigger execution. PostgreSQL also permits a row-level `BEFORE` trigger to raise an exception before the row is written; the explicit `check_violation` is used here so rejected authority writes are transaction-visible failures rather than silent skips.

The guard function remains `SECURITY DEFINER`, fixes its `search_path` to `pg_catalog, pg_temp`, and revokes PUBLIC execute. This does not grant accounting authority to Billing or another foreign context and does not alter posted facts, financial amounts, period transitions, retained snapshots, or reconciliation evidence.

## TDD and implementation evidence

Real-PostgreSQL RED `614d1164f3abf1f7bab3fe77d520e5b7108e4c69` creates a tenant period whose shared compatibility projection is `soft_closed`, inserts a later active accounting book, confirms that migration 0034 creates no control/fence for that non-open pair, then calls `_lock_book_period()`. The acceptance requires the call to fail with the existing `AccountingValidationError` and requires both control and fence populations to remain absent.

Production candidate `610d77082eb01c80d2e9e74521e48a3b06e1375a` installs the post-repair direct-insert guard in migration 0034. Static ratchet `ee233b5c40c942008c7ec034917fd49f1fcf9976` pins the structural nesting check, open/NULL-only invariant, `BEFORE INSERT` placement, and migration-repair ordering.

Application-source RED `tests/test_book_period_application_authority_contract.py` separately requires the persistence adapter to remove the stale `INSERT ... SELECT` writer, replace both shared-state `LEFT JOIN`/`COALESCE` fallbacks with an exact `accounting_book_period_control` join, lock the authoritative control row with `FOR UPDATE OF accounting_book_period_control`, and preserve unrelated reporting/integration surfaces in the large adapter. Real-PostgreSQL `tests/test_postgres_book_period_control_no_projection_red.py` supplies the buyer-relevant missing-non-open-control case.

Production source repair `048671fe7243b6bf8c730c349b46d4f3bfc79dde` is a normal descendant of `9086422c2cd801c3be76069114ee0e6753c47f92`. Exact commit comparison reports one modified path, `src/accounting_information_platform/persistence.py`, with 9 additions and 40 deletions. The patch is limited to the three authority helpers: removing the runtime control INSERT, replacing the two shared-state fallbacks with inner book-control joins, and updating helper documentation. It does not delete or rewrite the unrelated reporting, reconciliation, HomeTax, VAT, or ledger surfaces that the preservation contract protects.

Explicit-rejection RED `344180cf932a6d278f1b28a88d9b7b3a2714232e` strengthens the static contract and adds `tests/test_postgres_book_period_control_insert_authority_red.py`. The real PostgreSQL case creates a legitimate missing non-open book-period pair, attempts a raw literal-open control INSERT, and requires `psycopg.errors.CheckViolation` carrying `book_period_control_insert_authority_required`, followed by zero retained control rows. Production repair `b33b1879600ba088d2f4f7481c547ec99372456b` changes only the guard's rejected branch from silent `RETURN NULL` to that stable exception; canonical nested open-only seeding still returns `NEW` unchanged.

These SHAs are development lineage, not release evidence. The REDs were authored before their causal repairs, but queued or cancelled GitHub evidence does not become observed RED/GREEN by assertion. Exact-head real-PostgreSQL execution, security/SAST/dependency evidence, independent review, protected-stack prerequisites, migration/recovery evidence, and immutable release evidence remain separate gates.

## Scope-preservation repair

A source-cleanup candidate at `952bb1b2a014db823f8ee452ebdfb9bc3980e733` attempted to replace the three stale helper paths together. Exact-blob verification immediately found that the replacement did not preserve unrelated methods in the large persistence adapter, so that candidate is invalid development evidence and must not be used as a GREEN or release input.

Normal descendant `a8c5abe7520cb0a50708127726bcf0dfb420dc60` restored `src/accounting_information_platform/persistence.py` byte-for-byte to prior complete blob `1d27c2399b0adca1aead3a2f3a141a8eb6a95435`. No force-push, reset, destructive rebase, or selective loss of concurrent delta was used. Scope-preservation ratchet `9086422c2cd801c3be76069114ee0e6753c47f92` then fixed the acceptance before another production edit was attempted.

The successful source repair at `048671fe7243b6bf8c730c349b46d4f3bfc79dde` was applied against that exact restored blob. A post-write compare against `9086422c2cd801c3be76069114ee0e6753c47f92` shows `ahead_by=1`, `behind_by=0`, a single modified file, and 49 changed lines. This scope check is part of the verification record: changing an authority boundary is not acceptable if the patch silently deletes unrelated accounting behavior.

## Recovery and follow-up

A rejected direct control INSERT raises `check_violation`, writes no authoritative row, and therefore seeds no 64-row journal-population fence. The transaction is aborted until the caller rolls it back, preventing a caller from treating a rejected authority mutation as successful work. Operators must not repair a missing non-open pair by copying `fiscal_period` status or by manual SQL. If a later-created book must become applicable to an already non-open period, that requires an explicit book-period lifecycle/adoption/reopen command with authenticated capability, idempotency, maker-checker policy where applicable, and retained chronology.

With `048671fe7243b6bf8c730c349b46d4f3bfc79dde` and `b33b1879600ba088d2f4f7481c547ec99372456b`, the application and database share the same writer model: master-data seed triggers may create literal-open controls, while posting/adjusting/close helpers only consume existing book-period authority and rejected raw control writers receive an explicit database error. The source cleanup is not considered execution-GREEN until an unchanged exact head runs the static authority contract and real-PostgreSQL regressions successfully. Missing-control diagnostic wording for an adjusting journal remains a possible buyer-facing refinement, but it must not reintroduce authority synthesis.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18.6 documentation: System information functions and operators*. https://www.postgresql.org/docs/18/functions-info.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18.6 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18.6 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
