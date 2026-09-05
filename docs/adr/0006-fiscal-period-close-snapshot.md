# ADR 0006: Fiscal-period close is a snapshot-and-status transaction

**Status:** Superseded in part by ADR 0023

## Problem

Hard close certifies a retained trial-balance population as Accounting Information Platform evidence. That population must not be caller-shaped, mutable after close, cross an accounting-book aggregate boundary, drift arithmetically, or omit a journal that was validly admitted before close authority won.

The implementation also has to preserve posting throughput. `accounting_book_period_control` is one row per tenant/accounting-book/fiscal-period. Updating that row for every ordinary open-period journal would turn a correctness fence into a hot exclusive-write point and serialize otherwise independent postings in the busiest lifecycle state.

## Decision

`PostgresPostingLedger.close_fiscal_period` remains the first-class close command. ADR 0023 owns the two-step lifecycle: `soft_closed` changes period state only; it creates no `trial_balance_snapshot`, `trial_balance_line`, or mandatory closing journal. A later `hard_closed` command acquires the tenant/book/period command authority, writes a period-closing journal when required, derives the live trial balance from AIS-owned PostgreSQL facts through the period end, persists one retained snapshot population, and hard-closes the period in the governed transaction. Posted journals are never rewritten.

An exact hard-close replay returns the retained result and creates no second snapshot, journal, or close event. `hard_closed` cannot transition back to `soft_closed`. Soft-close replay remains snapshot-free.

### Retained population identity and immutability

Migration `0029_trial_balance_snapshot_population_unique_index.sql` builds the unique `(tenant_account_id, accounting_book_id, fiscal_period_id)` population identity with `CREATE UNIQUE INDEX CONCURRENTLY` outside a transaction block. Migration `0030_trial_balance_snapshot_immutability.sql` attaches that index as `trial_balance_snapshot_one_population_per_book_period`, installs header/line mutation guards, and serializes snapshot and line admission on the exact `accounting_book_period_control` row.

Snapshot and line UPDATE/DELETE fail with `trial_balance_snapshot_immutable`. New population after `hard_closed` is rejected. A visible competing population fails with `trial_balance_snapshot_population_conflict`; the unique constraint independently closes the stale-snapshot race when a `REPEATABLE READ` trigger query cannot see a concurrently committed population.

Every retained line must satisfy the exact PostgreSQL `numeric(38, 6)` invariant

`net_balance_amount = debit_total_amount - credit_total_amount`.

Migration 0030 adds `trial_balance_line_net_balance_conservation` as `NOT VALID`; migration `0031_trial_balance_line_conservation_validation.sql` validates inherited rows after 0030 has committed so the stronger ADD-CONSTRAINT lock is not retained through the validation scan.

### Aggregate and authority scope

A retained snapshot header must use the legal entity that owns the selected accounting book, and `snapshot_currency_code` must equal that book's `reporting_currency_code`. Every retained `trial_balance_line.chart_account_id` must belong to the same accounting book. Tenant-scoped identifiers that are independently valid cannot be recombined across those aggregate boundaries.

Snapshot creation is not an ordinary soft-close write. PostgreSQL admits a new snapshot only while the exact book-period is `soft_closed`, `session_user` belongs to `accounting_closing_writer`, and the transaction carries hard-close command context. That context is either transaction-local `accounting_core.journal_write_role=period_closing` after a required closing-journal write or the canonical tenant/resolved-accounting-book-id/period advisory lock held by `close_fiscal_period`. The GUC and lock classify the command; role membership remains the capability boundary.

The canonical advisory key is `hashtext(tenant_reference)` plus `hashtext('period:' || accounting_book_id::text || ':' || period_code)`. The trigger reconstructs the resolved accounting-book identity, not the caller-facing `book_name`. `snapshot_generated_at` is database-owned system time and is replaced with `clock_timestamp()` even for an otherwise authorized closing writer.

### Journal-population freshness without an open-period write hotspot

A different race exists between an admitted journal and hard close. `close_fiscal_period` uses `REPEATABLE READ`; a close transaction can therefore hold an MVCC snapshot that predates a journal committed while close is waiting on its period authority. Waiting alone does not refresh the repeatable-read snapshot.

Migration `0032_period_close_journal_population_fence.sql` uses the book-period control row as a lifecycle fence, but it does **not** update that row for every journal:

- when the period is `open`, journal admission takes `SELECT ... FOR SHARE` on the exact control row and returns without changing `journal_population_revision`; many open-period journals can hold this shared row lock concurrently, while a period-state UPDATE must wait for them to finish;
- if the period changes while an open-path journal is waiting for that shared lock, admission fails with SQLSTATE `40001` (`serialization_failure`) and the journal command must retry from a fresh transaction rather than inherit stale open-period authority;
- when the period is `soft_closed`, only purpose-limited `period_closing`, `adjusting`, or `reversal` journals from `accounting_closing_writer` are admitted, and those close-window journals increment `journal_population_revision` on the exact control row in the same transaction as the journal header;
- hard close later locks that same row. If a soft-close journal committed after the close transaction's repeatable-read snapshot, PostgreSQL rejects the stale close with serialization failure rather than freezing an older population. If hard close owns the row first, the later journal cannot remain admissible after the committed `hard_closed` state.

This split is intentional. Updating the control row for every ordinary journal would create a per-book-period exclusive UPDATE hotspot. `FOR SHARE` blocks status-changing UPDATEs while allowing other `FOR SHARE` holders, so the high-volume open-period path remains concurrent while the lower-volume close window receives the stronger row-version fence required to invalidate stale close snapshots.

A serialization failure is not accounting evidence. The whole failed transaction is rolled back and the identical idempotency key is retried from the beginning. `tests/test_postgres_period_close_journal_serialization_red.py` exercises stale hard-close rollback and exact-key retry against retained/live amounts. `tests/test_postgres_open_period_journal_fence.py` requires ordinary open-period posting to leave `journal_population_revision` unchanged. `tests/test_trial_balance_snapshot_immutability_contract.py` ratchets the migration shape, including the open-path shared lock and soft-close-only revision update.

## Alternatives considered

Updating `journal_population_revision` for every admitted journal was rejected after review because it makes one control row the exclusive write point for all ordinary posting in a book-period. It preserves freshness but violates the platform's hot-partition/lock and latency requirements.

Relying only on the period advisory lock was rejected because a waiting `REPEATABLE READ` close can retain the snapshot established before lock grant. Lock acquisition and snapshot freshness are separate concerns.

Using only a trigger-side existence query for retained snapshots was rejected because a fixed MVCC snapshot cannot observe a competing population committed after that snapshot. Physical unique population identity remains required.

Allowing caller-provided close timestamps, currencies, aggregate identifiers, or retained arithmetic was rejected because those values are accounting evidence and must be derived or verified at the authoritative database boundary.

## Consequences and operational evidence

Ordinary open-period posting now participates in period-transition coordination without incrementing a shared revision row on every journal. Period transition can wait on concurrent open journal transactions; this is deliberate because a journal admitted under `open` must commit before the transition can certify a different period state. Close-window journals remain serialized on the control-row revision because they are exceptional writes whose population must invalidate a stale hard-close snapshot.

Migration 0032 replaces `accounting_core.guard_period_insert()` as a `SECURITY DEFINER` function with `search_path = pg_catalog, pg_temp` and PUBLIC EXECUTE revoked. Unauthorized or rejected journals do not retain a revision change because the statement/transaction is rolled back.

The migration chain has distinct recovery states. A failed 0029 concurrent index build can leave an invalid index that operators must remove or rebuild before retry. A failed 0031 validation leaves the constraint enforced for subsequent writes but inherited rows uncertified. A 0032 serialization conflict leaves no hard-close evidence from the failed transaction and requires a whole-command retry. Release evidence must distinguish these states; recovery must never normalize or rewrite posted journals, reconciliation evidence, or retained close facts.

Future reopen/correction is not implemented here. Any later reopen policy must preserve the prior hard-close population through explicit successor lineage and a replacement population identity/version invariant rather than mutating the retained population or silently weakening uniqueness.

## Exact soft-close replay

Migration `0010_soft_close_command_evidence.sql` stores the original tenant-scoped soft-close idempotency key, source-journal count, and canonical close-source SHA-256 on the book-period control row in the same transaction as the state transition and outbox event. Exact replay returns those stored facts and never recomputes historical evidence from later ledger state. A different key for an already soft-closed book-period is an idempotency conflict.

`snapshot_currency_code` remains required on soft close because it participates in the canonical close-source digest even though soft close creates no retained trial-balance snapshot.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: Serialization failure handling*. https://www.postgresql.org/docs/18/mvcc-serialization-failure-handling.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: CREATE INDEX*. https://www.postgresql.org/docs/18/sql-createindex.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: ALTER TABLE*. https://www.postgresql.org/docs/18/sql-altertable.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: Unique indexes*. https://www.postgresql.org/docs/18/indexes-unique.html

PostgreSQL Global Development Group. (2026g). *PostgreSQL 18 documentation: pg_locks*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026h). *PostgreSQL 18 documentation: System administration functions—Advisory lock functions*. https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS
