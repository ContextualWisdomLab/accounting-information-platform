# ADR 0006: Fiscal-period close is a snapshot-and-status transaction

**Status:** Superseded in part by ADR 0023

## Decision

`PostgresPostingLedger.close_fiscal_period` is the first-class close command. The current authoritative sequence is the two-step rule defined by ADR 0023. A `soft_closed` command changes the fiscal-period status only: it writes no `trial_balance_snapshot`, no `trial_balance_line`, and no period-closing journal. A later `hard_closed` command posts the AIS period-closing journal first, computes the live trial balance for the tenant, legal entity, and accounting book through the period end date, persists exactly one `trial_balance_snapshot` population, and then hard-closes the period in the same governed transaction. Posted journals are never rewritten.

An exact hard-close replay returns the existing snapshot and writes no second snapshot, closing journal, or close event. A `hard_closed` period cannot transition back to `soft_closed`. Soft-close replay remains snapshot-free.

The retained hard-close population is one snapshot per tenant/accounting-book/fiscal-period authority scope. Migration `0029_trial_balance_snapshot_immutability.sql` serializes ordinary snapshot admission on the same `accounting_book_period_control` row used by close and rejects a visible pre-existing population with `trial_balance_snapshot_population_conflict`. It also adds the physical unique constraint `trial_balance_snapshot_one_population_per_book_period` over that exact scope. The declarative constraint is required because PostgreSQL `REPEATABLE READ` retains the transaction snapshot established by its first query or data-modification statement; waiting for the authority-row lock does not make a stale transaction see a snapshot row committed later. The unique constraint therefore closes the stale-snapshot race even when the trigger-level existence query cannot observe the competing row.

This is deliberately fail-closed. If raw or legacy SQL has inserted a snapshot before the canonical hard-close transaction, AIS does not silently adopt that row or create a later row and select whichever timestamp sorts last. A visible conflict raises `trial_balance_snapshot_population_conflict`; a fixed-snapshot concurrency conflict is rejected by the named unique constraint. In either case the hard-close transaction cannot establish a second population and the book-period remains non-hard-closed until conflicting retained evidence is resolved through an audited repair.

## Consequences

Controllers close books through the posting adapter instead of a raw status update. Ordinary posting is rejected for every non-open period. The database insert guard in `0005_closed_period_guard.sql` permits only purpose-limited AIS close/adjust/reversal writes while a period is `soft_closed`; every insert is rejected once the period is `hard_closed`. The caller-controlled `accounting_core.journal_write_role` GUC is classification metadata, not sufficient authorization by itself.

Migration `0029_trial_balance_snapshot_immutability.sql` also rejects UPDATE or DELETE of retained snapshot headers and lines and rejects population extension after hard close. Header and line admission lock the exact tenant/book/period authority row before evaluating status. That row lock serializes status-sensitive admission and gives the normal visible-conflict diagnostic; the exact-scope unique constraint independently enforces one population across transaction snapshots and concurrent writers. Pre-migration history is retained rather than rewritten and is not retroactively attested as canonical solely because the guard was installed. If duplicate populations already exist, installing the constraint fails rather than blessing ambiguous history.

The former snapshot-on-soft-close and snapshot-reuse-on-upgrade wording in this ADR is superseded by ADR 0023. Operational and reporting code must therefore treat the hard-close snapshot as the only persisted post-close trial-balance population and must never infer that a soft-close created one.

## Exact soft-close command replay

Soft-close deliberately stores no trial-balance snapshot, but it is still an authoritative state-changing command. Migration `0010_soft_close_command_evidence.sql` records the original tenant-scoped soft-close idempotency key, source-journal count and canonical close-source SHA-256 on the book-period control row in the same transaction as the state transition and outbox event. Exact replay returns those stored facts and never recomputes historical evidence from later ledger state. A different key for an already soft-closed book-period is an idempotency conflict, and database trigger protection prevents rewriting evidence once recorded.

`snapshot_currency_code` remains required on soft-close because it participates in the canonical close-source digest even though no hard-close snapshot row is created. This makes the command evidence exact without representing soft-close as a trial-balance snapshot.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: Unique indexes*. https://www.postgresql.org/docs/18/indexes-unique.html
