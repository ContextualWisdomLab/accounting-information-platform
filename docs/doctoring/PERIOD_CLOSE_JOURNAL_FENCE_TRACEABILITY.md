# Period Close journal-population fence traceability

## Decision scope

This note traces the concurrency control used when an authoritative journal population approaches fiscal-period close. It does not create accounting policy, reopen a closed period, grant Billing posting authority, or make PostgreSQL locking semantics an IFRS requirement.

IAS 10 distinguishes adjusting events that provide evidence about conditions existing at the reporting-period end from non-adjusting events arising later. The product-level implication already adopted by ADR 0006/ADR 0023 is narrower: a governed close must not certify retained balances while a journal that is validly admissible for that period is concurrently being committed. IAS 10 does **not** prescribe PostgreSQL row locks, MVCC isolation, retry codes, revision counters, or striped fence rows; those are AIP implementation controls.

## Authoritative technical basis

PostgreSQL 18 documents that `FOR SHARE` is compatible with another `FOR SHARE`/`FOR KEY SHARE`, while blocking `UPDATE`, `DELETE`, `FOR UPDATE`, and `FOR NO KEY UPDATE` on the same row. Row locks are held through transaction end. PostgreSQL also states that a row-lock request in `REPEATABLE READ` or `SERIALIZABLE` errors when the row to be locked changed after the transaction began. Serialization failures use SQLSTATE `40001`, and the complete transaction must be retried from the beginning rather than resumed after the failed statement.

The current PostgreSQL documentation line is PostgreSQL 18; PostgreSQL 18.6 was released on 2026-08-13. Minor-version operability evidence remains owned by the PostgreSQL runtime-baseline lane. This control relies on documented PostgreSQL 18 concurrency semantics and does not treat a clean CI image as evidence that an existing production cluster was upgraded safely.

## Control mapping

| Concern | Chosen control | Rejected alternative | Executable evidence |
|---|---|---|---|
| Ordinary `open` posting throughput | `guard_period_insert()` takes `FOR SHARE` on the exact book-period control row, then increments one of 64 pre-existing journal-population fence rows selected from the journal UUID | Increment one shared book-period revision for every journal; that serializes all ordinary posting on one row | `tests/test_postgres_open_period_journal_fence.py`; migration `0033_open_period_journal_population_fence.sql` |
| Direct `open` → close freshness | Before a period status transition commits, PostgreSQL locks all 64 pre-existing fence rows in slot order. A fence modified after the close transaction's `REPEATABLE READ` snapshot causes SQLSTATE `40001`, so stale close evidence rolls back | A shared control-row lock alone; it prevents transition overtaking an in-flight journal but carries no row version showing that a journal committed after the close snapshot | `tests/test_postgres_open_period_close_serialization_red.py` |
| Compatible open-path coordination | Open journals continue to share the control-row lock; only journals landing on the same stripe compete for the stripe UPDATE | No period fence; a state transition could certify an older journal population | `tests/test_postgres_open_period_journal_fence.py::test_open_period_posting_can_progress_while_peer_holds_share_fence` |
| State changes while an open journal waits | Fail with SQLSTATE `40001` and retry the whole journal command from a fresh transaction | Continue using stale open-state authority after waiting | migrations `0032`/`0033`; `tests/test_trial_balance_snapshot_immutability_contract.py` |
| `soft_closed` adjusting/reversal/closing journal freshness | Purpose-limited close-window journals increment `accounting_book_period_control.journal_population_revision` in the journal transaction | Advisory-lock wait only; a pre-existing repeatable-read snapshot remains stale after lock grant | `tests/test_postgres_period_close_journal_serialization_red.py` |
| Direct hard-close snapshot authority | A snapshot while the book-period is still `open` is admitted only when the session has `accounting_closing_writer` capability **and** holds the canonical tenant/resolved-book-id/period close advisory lock; a bare GUC/role cannot pre-populate open-period evidence | Require `soft_closed` for every snapshot, which breaks the published direct `open` → `hard_closed` command; or accept a bare role/GUC, which permits pre-population | migration `0033`; direct-open retry path in `tests/test_postgres_open_period_close_serialization_red.py` |
| Fence existence | 64 rows are seeded for every existing book-period before FORCE RLS is enabled, and an AFTER INSERT trigger seeds every future control row | Lazy per-journal fence creation; a stale repeatable-read snapshot could fail to see a newly created row and therefore fail to detect the concurrent journal | migration `0033`; static contract |
| Capability/security | Fence and guard functions are `SECURITY DEFINER`, fix `search_path = pg_catalog, pg_temp`, revoke PUBLIC execute, and the fence table is tenant RLS/FORCE RLS protected | Caller GUC alone as authorization or an unscoped shared fence | migration `0033`; static contract |

## Why migration 0032 alone was insufficient

Migration 0032 corrected the first stale-close race for `soft_closed` journals without preserving the rejected all-journal single-row UPDATE. Its `open` path deliberately left `journal_population_revision` unchanged and held only `FOR SHARE` on `accounting_book_period_control`.

That lock prevents a status UPDATE from overtaking a journal transaction that is still in flight, but it does not record that the journal committed after a close transaction established its `REPEATABLE READ` snapshot. A close can therefore establish an old snapshot, wait for an open journal's shared control-row lock, acquire the unchanged control row after the journal commits, and continue deriving close evidence from the older snapshot. Migration 0033 supplies a bounded, pre-existing row-version witness without restoring one global write hotspot.

The 64-slot count is an engineering trade-off, not an accounting standard and not a performance result. It bounds fence-row fan-out during the low-frequency state transition while reducing expected ordinary-post collisions relative to one shared row. Exact-head load testing still has to measure collision rate, lock waits, WAL/write cost, and buyer-path p95; the slot count must be changed only from measured evidence.

## TDD and repair lineage

- Soft-close journal / stale hard-close RED: `306f4c14212a0dfbb89a6934bbb493b1e179479e`.
- First freshness candidate through `84f5666aa48ba565fb2e4ff763bb0b3ee27fe857` updated the control-row revision for every journal.
- Single-row open-period hotspot RED: `6fe1fdeb1050111b26e557810dbf66b05f75871a`.
- Split control-row repair: `7a979845896869ef0e7fabab710c7a4f3a9863de`; compatible-lock regression `2bdb09a6e7da1444a2356d94ae7fde16d9d40686`.
- Direct open-period stale-close RED: `70a9b196da23fc0cbedd9ceafa806710794f13e3`.
- Initial 64-stripe database repair: `ac3a2a7eac2929e3ff76908d9bb64a4a38acb7dd`; canonical installer inclusion: `7cafddd91affeb0166956a96c260a8c17f06ac42`; static contract: `7279c9a45cb6e515a9e88b0171fb8390c823ccba`.
- Migration self-review moved the cross-tenant backfill before FORCE RLS so a non-superuser schema owner is not forced to borrow one runtime tenant scope while seeding all existing book-periods: `a6d32fc35f6f0f48fbcec6b08fa16b1a89eb5f80`.

These commits are development evidence, not protected-head release evidence. The direct-open RED was added before the causal repair, but queued GitHub runners have not provided an observed RED/GREEN transition for the current lineage. Exact-head PostgreSQL CI, security/SAST/dependency evidence, independent review, central workflow gates, package/SBOM/provenance, migration/recovery verification, and measured performance must be reacquired after every head change.

## Residual risk and release acceptance

Striping removes the deliberate single-row write hotspot but does not make row versioning free. PostgreSQL notes that row locking can cause writes, and same-slot journals still serialize on the selected stripe. Release acceptance therefore requires measured concurrent posting plus period-transition load at the exact candidate head, reporting lock waits, stripe collision distribution, retries, and tail latency rather than hiding them with cache warm-up, reduced samples, or excluded failures.

A `40001` result is not accounting evidence. The caller must retry the complete command with the same immutable source-payload identity/idempotency contract. A failed stale close must leave no retained snapshot, closing journal, close event, or authoritative state transition. Recovery must never rewrite posted journals, retained trial-balance evidence, or reconciliation authority rows to make a failed close appear successful.

## References

IFRS Foundation. (n.d.). *IAS 10 Events after the Reporting Period*. https://www.ifrs.org/issued-standards/list-of-standards/ias-10-events-after-the-reporting-period.html/content/

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: Serialization failure handling*. https://www.postgresql.org/docs/18/mvcc-serialization-failure-handling.html
