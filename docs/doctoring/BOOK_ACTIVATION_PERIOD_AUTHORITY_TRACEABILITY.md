# Book activation / open-period authority traceability

## Scope

This note records the post-install lifecycle edge where an already-recorded inactive `accounting_book` becomes active while one or more fiscal periods are open. It is a database master-data invariant, not an IFRS rule and not permission for application code, Reporting, Billing, or manual SQL to manufacture book-period close authority.

## Finding

Migration `0034_book_period_control_seed.sql` originally seeded `accounting_book_period_control` only from two creation events: insertion of an open `fiscal_period`, and insertion of an already-active `accounting_book`. That left a third supported state transition uncovered. A book inserted with `valid_to IS NOT NULL` can later become active through `UPDATE ... SET valid_to = NULL`; if the matching fiscal period already exists and is open, neither INSERT trigger runs. The result is an active-book/open-period intersection with no authoritative control row and therefore no 64-row `period_journal_population_fence` population.

Journal and close admission correctly fail closed when that population is missing, but permanent failure of a legitimate activation is not the intended invariant. The database owner of the book-period intersection must materialize the open authority at the activation boundary rather than waiting for a later posting/close helper to synthesize it.

## Selected control

Migration 0034 reuses `accounting_core.seed_book_period_control_for_book()` from a narrow lifecycle trigger:

```sql
AFTER UPDATE OF valid_to
ON accounting_core.accounting_book
FOR EACH ROW
WHEN (OLD.valid_to IS NOT NULL AND NEW.valid_to IS NULL)
```

The existing seeder remains open-only. It scans only `fiscal_period.period_status_code = 'open'`, inserts literal `period_status_code = 'open'` with `period_closed_at = NULL`, and the migration-0033 control-row trigger synchronously creates all 64 freshness-fence rows. Activation into an already non-open shared period therefore remains absent/fail-closed; no `soft_closed` or `hard_closed` state is inferred from the tenant/calendar compatibility projection.

Reusing the same seeder also preserves the tenant-row MVCC witness used by new-book/new-period concurrency repair. The `WHEN` predicate prevents unrelated book updates and deactivation from taking that low-frequency master-data serialization point.

## TDD / implementation evidence

- RED `795b3efef7d0d521719bb60b1de7d937232221e7` adds `tests/test_postgres_book_activation_seed_red.py`. It inserts an inactive book while period `2026-08` is open, verifies zero control/fence rows, activates the book, then requires exactly one literal-open/no-close-timestamp control and exactly 64 fence rows.
- GREEN candidate `ca98183ec03836bfefeeaa8524f42e037957c3a2` adds the activation trigger to migration 0034 and reuses the canonical open-only seeder.
- Static ratchet `ce73c8c8c64cf4aa59b8f8692ff487650501cfb8` requires the exact update edge and same seeder, while retaining the existing open-only projection, FORCE-RLS, hardened `SECURITY DEFINER`, installer, and tenant-MVCC contracts.

These SHAs are development evidence. The RED is realistic by source inspection, but it is not called runner-observed until the corresponding head actually executes. The candidate is not GREEN until one unchanged exact head passes the real PostgreSQL regression and the complete applicable Accounting Foundation/security/review gates.

## Recovery and ownership

A serialization failure during activation rolls back the activation and seed side effects together; retry the complete master-data command from a fresh transaction. Do not insert a control manually, copy shared fiscal-period close state, weaken RLS, or rewrite posted/reconciled/retained accounting evidence.

`accounting_book_period_control` remains the authoritative tenant/book/period close-state intersection. `fiscal_period.period_status_code` remains a compatibility projection after book-scoped authority exists. `PostgresPostingLedger._lock_book_period()`, `_load_book_period_state()`, and `_require_open_book_period_bounds()` must ultimately be read/lock/fail-closed consumers of that authority and must not recreate or fall back to shared close state.
