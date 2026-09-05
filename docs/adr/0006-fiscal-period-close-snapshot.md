# ADR 0006: Fiscal-period close is a snapshot-and-status transaction

**Status:** Superseded in part by ADR 0023

## Problem

Hard close certifies a retained trial-balance population as Accounting Information Platform evidence. That population must not be caller-shaped, mutable after close, cross an accounting-book aggregate boundary, drift arithmetically, or omit a journal that was validly admitted before close authority won.

The implementation also has to preserve posting throughput. `accounting_book_period_control` is one row per tenant/accounting-book/fiscal-period. Updating that row for every ordinary open-period journal would turn a correctness fence into a hot exclusive-write point and serialize otherwise independent postings in the busiest lifecycle state. A shared lock on that row alone is also insufficient: it prevents a period transition from overtaking a still-running journal, but it does not prove that a journal committed after a close transaction established its `REPEATABLE READ` snapshot.

## Decision

`PostgresPostingLedger.close_fiscal_period` remains the first-class close command. ADR 0023 owns the lifecycle: `soft_closed` changes period state only and creates no retained trial-balance population. `hard_closed` acquires tenant/resolved-book/period command authority, writes a period-closing journal when required, derives the live trial balance from AIS-owned PostgreSQL facts through the period end, persists one retained snapshot population, and hard-closes the period in one governed transaction. Direct `open` → `hard_closed` remains supported; `open` → `soft_closed` → `hard_closed` is also supported. Posted journals are never rewritten.

An exact hard-close replay returns the retained result and creates no second snapshot, journal, or close event. `hard_closed` cannot transition back to `soft_closed`. Soft-close replay remains snapshot-free.

### Retained population identity and immutability

Migration `0029_trial_balance_snapshot_population_unique_index.sql` builds the unique `(tenant_account_id, accounting_book_id, fiscal_period_id)` population identity with `CREATE UNIQUE INDEX CONCURRENTLY` outside a transaction block. Migration `0030_trial_balance_snapshot_immutability.sql` attaches that index as `trial_balance_snapshot_one_population_per_book_period`, installs header/line mutation guards, and serializes snapshot and line admission on the exact `accounting_book_period_control` row.

Snapshot and line UPDATE/DELETE fail with `trial_balance_snapshot_immutable`. New population after `hard_closed` is rejected. A visible competing population fails with `trial_balance_snapshot_population_conflict`; the unique constraint independently closes the stale-snapshot race when a `REPEATABLE READ` trigger query cannot see a concurrently committed population.

Every retained line must satisfy the exact PostgreSQL `numeric(38, 6)` invariant

`net_balance_amount = debit_total_amount - credit_total_amount`.

Migration 0030 adds `trial_balance_line_net_balance_conservation` as `NOT VALID`; migration `0031_trial_balance_line_conservation_validation.sql` validates inherited rows after 0030 has committed so the stronger ADD-CONSTRAINT lock is not retained through the validation scan.

### Aggregate and authority scope

A retained snapshot header must use the legal entity that owns the selected accounting book, and `snapshot_currency_code` must equal that book's `reporting_currency_code`. Every retained `trial_balance_line.chart_account_id` must belong to the same accounting book. Tenant-scoped identifiers that are independently valid cannot be recombined across those aggregate boundaries.

The canonical hard-close advisory key is `hashtext(tenant_reference)` plus `hashtext('period:' || accounting_book_id::text || ':' || period_code)`. PostgreSQL reconstructs the resolved accounting-book identity rather than trusting caller-facing `book_name`. `snapshot_generated_at` is database-owned system time and is replaced with `clock_timestamp()` even for an otherwise authorized closing writer.

Migration `0033_open_period_journal_population_fence.sql` preserves the purpose-limited snapshot writer while restoring the supported direct `open` → `hard_closed` path. A snapshot created while the book-period is still `open` requires `accounting_closing_writer` capability **and** the exact tenant/resolved-book/period close advisory lock; a bare role plus `accounting_core.journal_write_role` cannot pre-populate open-period retained evidence. While `soft_closed`, the existing purpose-limited `period_closing` command context or the canonical close lock remains sufficient together with role membership.

### Journal-population freshness without a single-row posting hotspot

`close_fiscal_period` uses `REPEATABLE READ`. A close transaction can therefore hold an MVCC snapshot that predates a journal committed while close is waiting on period authority. Waiting for a lock does not refresh that snapshot.

Migration `0032_period_close_journal_population_fence.sql` first split the control profile:

- an `open` journal takes `SELECT ... FOR SHARE` on the exact book-period control row so a state transition cannot overtake a journal already admitted as open;
- a purpose-limited `soft_closed` period-closing/adjusting/reversal journal increments `journal_population_revision` on that control row, so a stale hard close that later locks the row fails with SQLSTATE `40001`.

Review then exposed a remaining direct-open race. The open journal changed no row visible to a stale close. After waiting for the shared control-row lock, the close could acquire the unchanged row and continue from its older MVCC snapshot.

Migration `0033_open_period_journal_population_fence.sql` adds a bounded row-version witness without restoring one global write hotspot:

- every book-period owns exactly 64 pre-existing `period_journal_population_fence` rows; migration backfill creates them before FORCE RLS is enabled, and an AFTER INSERT trigger seeds future book-period controls;
- after confirming `open` under `FOR SHARE`, each journal increments exactly one fence row selected from its database journal UUID; two journals contend only when they choose the same slot;
- before `period_status_code` changes, a transition trigger locks all 64 rows in deterministic slot order with `FOR UPDATE` and requires the complete population;
- if any fence row was committed after the close transaction's repeatable-read snapshot, PostgreSQL raises serialization failure instead of allowing the stale transition to commit;
- if close owns the control row first, a later open journal waits and then cannot retain stale open-state admission after the transition.

The 64-slot count is a measured-performance hypothesis, not accounting policy and not an IFRS requirement. It bounds the low-frequency transition fan-out while reducing expected ordinary-post collisions relative to one shared row. Exact-head load tests must still measure slot collisions, lock waits, WAL/write cost, retry rate, and buyer-path p95. A future slot-count change requires measured evidence and a migration-compatible design.

A serialization failure is not accounting evidence. The entire transaction rolls back and the command retries from the beginning with the same immutable source-payload identity and idempotency key. No failed close may leave a retained snapshot, period-closing journal, close event, or authoritative period transition.

`tests/test_postgres_period_close_journal_serialization_red.py` exercises a `soft_closed` adjustment racing hard close. `tests/test_postgres_open_period_close_serialization_red.py` exercises a journal committed after a direct close snapshot but before the close acquires the period row, then requires exact-key retry to retain the live population. `tests/test_postgres_open_period_journal_fence.py` protects ordinary open-path concurrency, and `tests/test_trial_balance_snapshot_immutability_contract.py` ratchets the migration/security shape.

## Alternatives considered

Updating one `journal_population_revision` for every admitted journal was rejected because it makes one book-period row the exclusive write point for all ordinary posting. It preserves freshness but violates the platform's hot-partition/lock and latency goals.

Using only the control-row `FOR SHARE`/`FOR UPDATE` protocol was rejected after the direct-open race review. It orders transaction completion around the state change but supplies no row version proving that an open journal committed after a pre-existing repeatable-read snapshot.

Relying only on the period advisory lock was rejected because a waiting repeatable-read close can retain the snapshot established before lock grant. Advisory-lock ownership and MVCC freshness are separate facts.

A session-lock-before-snapshot protocol was considered because reconciliation lifecycle already uses a committed session-lease pattern. It was not selected here because the required journal admission coordination can remain a database-owned book-period invariant without extending application-session lock lifetime across transaction boundaries. The striped witness also keeps correctness at the SQL boundary for purpose-limited writers. This decision can be revisited if measured stripe contention or transition fan-out is unacceptable.

Lazy creation of a fence row during journal admission was rejected because a repeatable-read close whose snapshot predates that INSERT can fail to see the new row. The complete fence population must exist before any journal/close race.

Using only a trigger-side existence query for retained snapshots was rejected because a fixed MVCC snapshot cannot observe a competing population committed after that snapshot. Physical unique population identity remains required.

Allowing caller-provided close timestamps, currencies, aggregate identifiers, or retained arithmetic was rejected because those values are accounting evidence and must be derived or verified at the authoritative database boundary.

## Consequences and operational evidence

Open-period posting now performs a shared control-row lock plus one striped revision UPDATE rather than one exclusive UPDATE on the common book-period row. This removes the deliberate single-row hotspot but does not prove the p95 ≤ 20 ms buyer target. PostgreSQL row locking can itself cause writes, and same-slot journals can still queue. Release evidence must therefore use realistic concurrent posting and transition workloads and report failures, retry rates, lock waits, stripe distribution, and tail latency without sample reduction or excluded errors.

Migration 0033 creates a new tenant-scoped table under RLS/FORCE RLS. The cross-tenant migration backfill occurs before FORCE RLS is enabled so a non-superuser schema owner can seed every existing book-period without impersonating one runtime tenant. Future rows are seeded from the book-period-control INSERT transaction and stay in that tenant scope. Guard functions are `SECURITY DEFINER`, use `search_path = pg_catalog, pg_temp`, and revoke PUBLIC execute.

The migration chain has distinct recovery states. A failed 0029 concurrent index build can leave an invalid index that operators must remove or rebuild before retry. A failed 0031 validation leaves the constraint enforced for subsequent writes but inherited rows uncertified. A failed 0033 migration transaction rolls back its table, trigger, function, policy, and seed population together. A runtime SQLSTATE `40001` leaves no authoritative close result and requires a whole-command retry. Recovery must never normalize or rewrite posted journals, reconciliation evidence, or retained close facts.

Future reopen/correction is not implemented here. Any later reopen policy must preserve the prior hard-close population through explicit successor lineage and a replacement population identity/version invariant rather than mutating retained evidence or weakening uniqueness.

## Exact soft-close replay

Migration `0010_soft_close_command_evidence.sql` stores the original tenant-scoped soft-close idempotency key, source-journal count, and canonical close-source SHA-256 on the book-period control row in the same transaction as the state transition and outbox event. Exact replay returns those stored facts and never recomputes historical evidence from later ledger state. A different key for an already soft-closed book-period is an idempotency conflict.

`snapshot_currency_code` remains required on soft close because it participates in the canonical close-source digest even though soft close creates no retained trial-balance snapshot.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: Serialization failure handling*. https://www.postgresql.org/docs/18/mvcc-serialization-failure-handling.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: CREATE INDEX*. https://www.postgresql.org/docs/18/sql-createindex.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: ALTER TABLE*. https://www.postgresql.org/docs/18/sql-altertable.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: Unique indexes*. https://www.postgresql.org/docs/18/indexes-unique.html

PostgreSQL Global Development Group. (2026g). *PostgreSQL 18 documentation: pg_locks*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026h). *PostgreSQL 18 documentation: Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS
