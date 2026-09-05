# Period Close journal-population fence traceability

## Decision scope

This note traces the concurrency control used when an authoritative journal population approaches fiscal-period close. It does not create accounting policy, reopen a closed period, grant Billing posting authority, or make PostgreSQL locking semantics an IFRS requirement.

IAS 10 distinguishes adjusting events that provide evidence about conditions existing at the reporting-period end from non-adjusting events arising later. The product-level implication already adopted by ADR 0006/ADR 0023 is narrower: a governed close must not certify retained balances while a journal that is validly admissible for that period is concurrently being committed. IAS 10 does **not** prescribe PostgreSQL row locks, advisory locks, MVCC isolation, retry codes, revision counters, or striped fence rows; those are AIP implementation controls.

## Authoritative technical basis

PostgreSQL 18 documents that `FOR SHARE` is compatible with another `FOR SHARE`/`FOR KEY SHARE`, while blocking `UPDATE`, `DELETE`, `FOR UPDATE`, and `FOR NO KEY UPDATE` on the same row. Row locks are held through transaction end. PostgreSQL also states that a row-lock request in `REPEATABLE READ` or `SERIALIZABLE` errors when the row to be locked changed after the transaction began. Serialization failures use SQLSTATE `40001`, and the complete transaction must be retried from the beginning rather than resumed after the failed statement.

The current PostgreSQL documentation line is PostgreSQL 18; PostgreSQL 18.6 was released on 2026-08-13. Minor-version operability evidence remains owned by the PostgreSQL runtime-baseline lane. This control relies on documented PostgreSQL 18 concurrency semantics and does not treat a clean CI image as evidence that an existing production cluster was upgraded safely.

## Control mapping

| Concern | Chosen / required control | Rejected alternative or current finding | Executable evidence |
|---|---|---|---|
| Database admission for ordinary `open` journals | `guard_period_insert()` takes `FOR SHARE` on the exact book-period control row, then increments one of 64 pre-existing journal-population fence rows selected from the journal UUID | Increment one shared book-period revision for every journal; that serializes all ordinary posting on one row | `tests/test_postgres_open_period_journal_fence.py`; migration `0033_open_period_journal_population_fence.sql` |
| Application-path ordinary posting concurrency | Ordinary Billing-owned proposals must not acquire the same exclusive tenant/book/period advisory mutex used to serialize close commands; the database fence is the final journal/transition authority | **Current repair finding:** `_require_open_book_period_bounds()` still calls `_acquire_command_lock(connection, f"period:{book_id}:{period_code}")`, so otherwise independent ordinary postings in one book-period are serialized before the striped database boundary | `tests/test_postgres_open_period_journal_fence.py::test_open_period_postings_do_not_serialize_on_application_period_lock`; `tests/test_open_period_application_lock_contract.py` |
| Direct `open` → close freshness | Before a period status transition commits, PostgreSQL locks all 64 pre-existing fence rows in slot order. A fence modified after the close transaction's `REPEATABLE READ` snapshot causes SQLSTATE `40001`, so stale close evidence rolls back | A shared control-row lock alone; it prevents transition overtaking an in-flight journal but carries no row version showing that a journal committed after the close snapshot | `tests/test_postgres_open_period_close_serialization_red.py` |
| Compatible database open-path coordination | Open journals share the control-row lock; only journals landing on the same stripe compete for the stripe UPDATE | Treating the database lock profile as proof of end-to-end posting parallelism while the application still holds an exclusive advisory period lock | `tests/test_postgres_open_period_journal_fence.py::test_open_period_posting_can_progress_while_peer_holds_share_fence` |
| State changes while an open journal waits | Fail with SQLSTATE `40001` and retry the whole journal command from a fresh transaction | Continue using stale open-state authority after waiting | migrations `0032`/`0033`; `tests/test_trial_balance_snapshot_immutability_contract.py` |
| `soft_closed` adjusting/reversal/closing journal freshness | Purpose-limited close-window journals increment `accounting_book_period_control.journal_population_revision` in the journal transaction | Advisory-lock wait only; a pre-existing repeatable-read snapshot remains stale after lock grant | `tests/test_postgres_period_close_journal_serialization_red.py` |
| Direct hard-close snapshot authority | A snapshot while the book-period is still `open` is admitted only when the session has `accounting_closing_writer` capability **and** holds the canonical tenant/resolved-book-id/period close advisory lock; a bare GUC/role cannot pre-populate open-period evidence | Require `soft_closed` for every snapshot, which breaks the published direct `open` → `hard_closed` command; or accept a bare role/GUC, which permits pre-population | migration `0033`; `tests/test_postgres_open_period_close_serialization_red.py` |
| Book-period authority existence | Every active book-period pair must have one `accounting_book_period_control` row before posting or close evaluation. Migration 0034 backfills missing pairs and seeds them transactionally when either a new fiscal period or a new active accounting book is inserted | Treat migration 0009's one-time backfill as sufficient. It leaves post-install master data without a control row, so migration 0033 correctly fails closed but ordinary posting becomes unavailable | migration `0034_book_period_control_seed.sql`; `tests/test_book_period_control_seed.py` |
| Fence existence | Migration 0033 seeds 64 rows for every control row, and migration 0034 guarantees that future active book-period pairs create that control row before journal admission | Lazy per-journal fence creation; a stale repeatable-read snapshot could fail to see a newly created row and therefore fail to detect the concurrent journal | migrations `0033`/`0034`; `tests/test_book_period_control_seed.py`; installer/static/PostgreSQL contracts |
| Capability/security | Fence and guard functions are `SECURITY DEFINER`, fix `search_path = pg_catalog, pg_temp`, revoke PUBLIC execute, and the fence table is tenant RLS/FORCE RLS protected | Caller GUC alone as authorization or an unscoped shared fence | migration `0033`; static contract |

## Why migration 0032 alone was insufficient

Migration 0032 corrected the first stale-close race for `soft_closed` journals without preserving the rejected all-journal single-row UPDATE. Its `open` path deliberately left `journal_population_revision` unchanged and held only `FOR SHARE` on `accounting_book_period_control`.

That row lock is not itself a freshness witness. A journal path that does not share the close advisory mutex can commit after a close transaction establishes its `REPEATABLE READ` snapshot. The close may then continue with an older journal population unless a pre-existing row version exposes that commit to PostgreSQL's repeatable-read conflict detection. The direct-open regression uses the AIS adjusting-journal path for exactly this reason: that command has its own idempotency mutex and can legitimately write while the book-period is `open`; it does **not** acquire the ordinary Billing proposal's tenant/book/period advisory lock. Migration 0033 supplies a bounded, pre-existing row-version witness for that database-authority race.

The 64-slot count is an engineering trade-off, not an accounting standard and not a performance result. It bounds fence-row fan-out during the low-frequency state transition while reducing expected ordinary-post collisions relative to one shared revision row. Exact-head load testing still has to measure collision rate, lock waits, WAL/write cost, and buyer-path p95; the slot count must be changed only from measured evidence.

## Why migration 0034 is required

Migration 0009 created `accounting_book_period_control` and backfilled only the accounting books and fiscal periods that existed at installation time. Migration 0033 correctly requires that control row and its complete 64-row fence population to pre-exist before journal admission; it must not lazily create a witness after a close transaction may already have established its snapshot.

That combination exposed a lifecycle gap for master data created after migration installation. A later `fiscal_period` could be opened while active books already existed, or a later active `accounting_book` could be created after fiscal periods existed. In either order there was no database command that materialized the Cartesian book-period control pair before posting. The journal guard would therefore fail closed with missing period-control authority even though the business period was legitimately open.

Migration 0034 keeps ownership in the database control boundary. An `AFTER INSERT` trigger on `fiscal_period` creates controls for all active books of the same tenant; a complementary `AFTER INSERT` trigger on `accounting_book` creates controls for all existing periods when the new book is active. Both use conflict-safe inserts. Each newly inserted control row synchronously invokes migration 0033's fence seeder, so all 64 stripes exist in the same transaction before the new master-data row becomes visible. The migration also backfills any active book-period pairs missed by earlier installation order. No journal amount, accounting policy, or foreign commercial truth is derived by these seeders.

## Remaining application serialization finding

The database repair does not yet remove every high-volume serialization point. `PostgresPostingLedger._require_open_book_period_bounds()` still acquires the canonical exclusive advisory lock `period:{book_id}:{period_code}` for every ordinary proposal. `close_fiscal_period()` deliberately uses the same identity to serialize close commands. Consequently two unrelated Billing proposals for the same open book-period can queue at the application boundary before either reaches the 64-stripe database fence.

This is a separate defect from the stale-close correctness race. The source repair must remove that close-command advisory mutex from the ordinary open-posting helper while preserving both open-state checks. The database `FOR SHARE` + striped revision boundary then owns journal-versus-transition ordering, while the period advisory lock remains for close-command serialization. The repair must not weaken idempotency locks, snapshot authority, role checks, or the database transition fence.

Real-PostgreSQL RED `1683fd5f8e21e907a187bea7c239e3d30f8d0bdb` pauses one ordinary proposal after open-period admission but before journal persistence, then requires a second ordinary proposal to complete before the first is released. Current source is expected to fail that contract because both commands take the same exclusive period advisory mutex. Static RED `839e930a4f24eda1083742578894479a8ed968bf` pins the causal source requirement directly. These REDs are not GREEN until the production helper is changed and exact-head PostgreSQL execution proves the overlap.

## TDD and repair lineage

- Soft-close journal / stale hard-close RED: `306f4c14212a0dfbb89a6934bbb493b1e179479e`.
- First freshness candidate through `84f5666aa48ba565fb2e4ff763bb0b3ee27fe857` updated the control-row revision for every journal.
- Single-row database hotspot RED: `6fe1fdeb1050111b26e557810dbf66b05f75871a`.
- Split control-row repair: `7a979845896869ef0e7fabab710c7a4f3a9863de`; compatible-row-lock regression `2bdb09a6e7da1444a2356d94ae7fde16d9d40686`.
- Direct open-period stale-close RED using the adjusting-journal path: `70a9b196da23fc0cbedd9ceafa806710794f13e3`.
- Initial 64-stripe database repair: `ac3a2a7eac2929e3ff76908d9bb64a4a38acb7dd`; canonical installer inclusion: `7cafddd91affeb0166956a96c260a8c17f06ac42`; static contract: `7279c9a45cb6e515a9e88b0171fb8390c823ccba`.
- Migration self-review moved the cross-tenant backfill before FORCE RLS: `a6d32fc35f6f0f48fbcec6b08fa16b1a89eb5f80`.
- Direct-open authority/fence-completeness PostgreSQL cases: `35c76f4dfda3b2d299b82eb28e06a9c2a9a6ba49`.
- End-to-end application advisory serialization RED: `1683fd5f8e21e907a187bea7c239e3d30f8d0bdb`; causal source ratchet: `839e930a4f24eda1083742578894479a8ed968bf`.
- Post-install master-data control/fence RED: `e5f40ca368a60394d0975d75baf249edfe876552`; database-owned dual-side seeding repair: `e22c2a6d9ad945eba986ec81f599cbd7dea60392`; canonical installer inclusion: `0d619c27cc2a0f56d501ed33fe50ed8746f4f2e9`.

These commits are development evidence, not protected-head release evidence. The REDs were committed before their respective causal repairs, but queued GitHub runners have not supplied an observed RED/GREEN transition for the current application-lock finding or the new book-period seeding repair. Exact-head PostgreSQL CI, security/SAST/dependency evidence, independent review, central workflow gates, package/SBOM/provenance, migration/recovery verification, and measured performance must be reacquired after every head change.

## Residual risk and release acceptance

Migrations 0033/0034 remove the deliberate **database** single-row revision hotspot and ensure its authority rows exist for post-install master data, but they do not make end-to-end posting concurrent while the application period advisory mutex remains. That source finding is therefore release-blocking for the stated hot-path goal and must not be hidden behind database-only lock evidence.

After the source repair, striping still has a cost. PostgreSQL notes that row locking can cause writes, and same-slot journals can still serialize on the selected stripe. Release acceptance therefore requires measured concurrent posting plus period-transition load at the exact candidate head, reporting advisory-lock waits, row-lock waits, stripe collision distribution, retries, WAL/write cost, and tail latency rather than hiding them with cache warm-up, reduced samples, or excluded failures.

A `40001` result is not accounting evidence. The caller must retry the complete command with the same immutable source-payload identity/idempotency contract. A failed stale close must leave no retained snapshot, closing journal, close event, or authoritative state transition. Recovery must never rewrite posted journals, retained trial-balance evidence, or reconciliation authority rows to make a failed close appear successful.

## References

IFRS Foundation. (n.d.). *IAS 10 Events after the Reporting Period*. https://www.ifrs.org/issued-standards/list-of-standards/ias-10-events-after-the-reporting-period.html/content/

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: Serialization failure handling*. https://www.postgresql.org/docs/18/mvcc-serialization-failure-handling.html
