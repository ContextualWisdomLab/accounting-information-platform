# ADR 0006: Fiscal-period close is a snapshot-and-status transaction

**Status:** Superseded in part by ADR 0023

## Decision

`PostgresPostingLedger.close_fiscal_period` is the first-class close command. The current authoritative sequence is the two-step rule defined by ADR 0023. A `soft_closed` command changes the fiscal-period status only: it writes no `trial_balance_snapshot`, no `trial_balance_line`, and no period-closing journal. A later `hard_closed` command posts the AIS period-closing journal first, computes the live trial balance for the tenant, legal entity, and accounting book through the period end date, persists exactly one `trial_balance_snapshot` population, and then hard-closes the period in the same governed transaction. Posted journals are never rewritten.

An exact hard-close replay returns the existing snapshot and writes no second snapshot, closing journal, or close event. A `hard_closed` period cannot transition back to `soft_closed`. Soft-close replay remains snapshot-free.

The retained hard-close population is one snapshot per tenant/accounting-book/fiscal-period authority scope. Migration `0029_trial_balance_snapshot_population_unique_index.sql` builds the exact-scope unique index with `CREATE UNIQUE INDEX CONCURRENTLY` outside a transaction block so an upgrade does not hold a write-blocking table lock for the duration of the index build. Migration `0030_trial_balance_snapshot_immutability.sql` attaches that already-built index as the named `trial_balance_snapshot_one_population_per_book_period` table constraint in a short transaction, installs the snapshot/line mutation guards, serializes snapshot admission on the same `accounting_book_period_control` row used by close, and rejects a visible pre-existing population with `trial_balance_snapshot_population_conflict`.

Snapshot creation is not an ordinary soft-close write. The database admits a new snapshot only while the exact book-period is `soft_closed`, the transaction-local `accounting_core.journal_write_role` is exactly `period_closing`, and `session_user` is a member of the purpose-limited `accounting_closing_writer` role. The GUC remains classification metadata rather than authority by itself. This preserves the production hard-close sequence, where the AIS period-closing journal establishes the same transaction-local classification before the snapshot is persisted, while a raw SQL session cannot pre-populate retained close evidence merely because the period is soft-closed.

The physical uniqueness boundary is required because PostgreSQL `REPEATABLE READ` retains the transaction snapshot established by its first query or data-modification statement; waiting for the authority-row lock does not make a stale transaction see a snapshot row committed later. The unique index therefore closes the stale-snapshot race even when the trigger-level existence query cannot observe the competing row. PostgreSQL documents that ordinary index creation can block writers for an unacceptable period on a live production table, while `CREATE INDEX CONCURRENTLY` keeps ordinary inserts, updates, and deletes available at the cost of extra scans and longer build time. PostgreSQL also documents converting a concurrently built unique index into a `UNIQUE` constraint with `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX` as the low-blocking deployment pattern.

This is deliberately fail-closed. Raw or legacy SQL cannot create a pre-close snapshot unless it also holds the purpose-limited closing capability and the exact transaction classification. A visible competing population raises `trial_balance_snapshot_population_conflict`; a fixed-snapshot concurrency conflict is rejected by the named unique boundary. In either case the hard-close transaction cannot establish a second population and the book-period remains non-hard-closed until conflicting retained evidence is resolved through an audited repair.

## Consequences

Controllers close books through the posting adapter instead of a raw status update. Ordinary posting is rejected for every non-open period. The database insert guard in `0005_closed_period_guard.sql` permits only purpose-limited AIS close/adjust/reversal journal writes while a period is `soft_closed`; every journal insert is rejected once the period is `hard_closed`. The caller-controlled `accounting_core.journal_write_role` GUC is classification metadata, not sufficient authorization by itself.

Migration `0030_trial_balance_snapshot_immutability.sql` rejects UPDATE or DELETE of retained snapshot headers and lines, rejects population extension after hard close, and rejects a snapshot header unless the soft-closed period is being written by the purpose-limited `period_closing` capability. Header and line admission lock the exact tenant/book/period authority row before evaluating status. That row lock serializes status-sensitive admission and gives the normal visible-conflict diagnostic; the exact-scope unique constraint independently enforces one population across transaction snapshots and concurrent writers. Pre-migration history is retained rather than rewritten and is not retroactively attested as canonical solely because the guard was installed. If duplicate populations already exist, the concurrent unique-index build fails rather than blessing ambiguous history.

The concurrent index phase is intentionally separate from the transactional trigger/constraint phase because PostgreSQL forbids `CREATE INDEX CONCURRENTLY` inside a transaction block. The canonical installer applies each forward migration file separately on an autocommit connection, so migration 0029 is one outside-transaction statement and migration 0030 remains an atomic transaction. A failed concurrent unique build may leave an invalid index, which PostgreSQL requires operators to remove or rebuild before retry; recovery tooling for that partial-upgrade state remains a release-readiness requirement and must not be represented as automatic rollback.

The former snapshot-on-soft-close and snapshot-reuse-on-upgrade wording in this ADR is superseded by ADR 0023. Operational and reporting code must therefore treat the hard-close snapshot as the only persisted post-close trial-balance population and must never infer that a soft-close created one.

Future fiscal-period reopen/correction is not implemented by this ADR. If a later policy introduces reopen, it must preserve the prior hard-close population and add an explicit successor lineage; it must not mutate retained evidence or silently relax the one-population constraint without a replacement identity/version invariant and migration plan.

## Exact soft-close command replay

Soft-close deliberately stores no trial-balance snapshot, but it is still an authoritative state-changing command. Migration `0010_soft_close_command_evidence.sql` records the original tenant-scoped soft-close idempotency key, source-journal count and canonical close-source SHA-256 on the book-period control row in the same transaction as the state transition and outbox event. Exact replay returns those stored facts and never recomputes historical evidence from later ledger state. A different key for an already soft-closed book-period is an idempotency conflict, and database trigger protection prevents rewriting evidence once recorded.

`snapshot_currency_code` remains required on soft-close because it participates in the canonical close-source digest even though no hard-close snapshot row is created. This makes the command evidence exact without representing soft-close as a trial-balance snapshot.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: CREATE INDEX*. https://www.postgresql.org/docs/18/sql-createindex.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: ALTER TABLE*. https://www.postgresql.org/docs/18/sql-altertable.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Unique indexes*. https://www.postgresql.org/docs/18/indexes-unique.html
