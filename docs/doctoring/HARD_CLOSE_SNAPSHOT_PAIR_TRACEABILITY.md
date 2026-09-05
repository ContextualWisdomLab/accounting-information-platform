# Hard-close / retained-snapshot pair traceability

## Finding

`accounting_book_period_control` is the authoritative tenant/book/period close-state fact. Migration 0035 already prevented the retained `trial_balance_snapshot` side of a hard close from committing unless the matching control ended `hard_closed`, but the inverse was not enforced: a writer could update an existing control to `hard_closed` and commit without a retained snapshot. The application later detected that damaged state in `_replay_close_receipt()`, but detection after commit is weaker than preventing an impossible accounting-control state.

This is an Accounting Information Platform database/DDD invariant. IFRS does not prescribe PostgreSQL trigger timing or this physical pairing mechanism.

## Test-first lineage

- `bea21ed65d9c9e8a79cb48a102f7688032baae0a` adds `tests/test_postgres_hard_close_snapshot_pair_red.py`. Starting from a legitimate `soft_closed` book-period with no snapshot, it attempts a direct `hard_closed` transition and requires commit to fail with `hard_close_snapshot_pair_required`; after rollback the authoritative status must remain `soft_closed` and snapshot count must remain zero.
- `26e71eb4e5a8450159f5ced482de43176c80e0f6` adds migration `0036_hard_close_trial_balance_snapshot_pair.sql`.
- `a3c2b250f95bc1232edb305c9fa03904ad22332c` puts migration 0036 in the canonical foundation installer.
- `b7056cd969693f9ccdd71f9e2958eedb1d9b133a` extends the static contract so every supported install requires both directions of the pair.
- `80a719fe85239b864993023d4de8aa4e5765b397` records the bidirectional commit invariant and recovery semantics in ADR 0006.

The first commit is a source RED by construction; it is not called runner-observed RED until the exact commit or a descendant containing that unchanged test executes it in PostgreSQL. Likewise, the migration is a production candidate until an unchanged exact head produces terminal GREEN evidence.

## Selected control

Migration 0036 installs an `AFTER UPDATE OF period_status_code` constraint trigger on `accounting_core.accounting_book_period_control`. Only a transition from a non-`hard_closed` state to `hard_closed` is queued. At deferred execution, the trigger requires a `trial_balance_snapshot` with the same tenant, accounting book, and fiscal period. Missing evidence raises `check_violation` with the stable marker `hard_close_snapshot_pair_required`.

The trigger is `DEFERRABLE INITIALLY DEFERRED`, `FOR EACH ROW`, and uses a `SECURITY DEFINER` function with `search_path = pg_catalog, pg_temp`; PUBLIC execute is revoked. PostgreSQL 18 documents that constraint triggers are `AFTER ROW` triggers whose firing can be deferred to transaction end, and that a constraint-trigger `WHEN` expression is evaluated immediately after the row update before a matching firing is queued. That is the required timing here: the canonical hard-close command inserts its snapshot first and advances the book-period control later in the same transaction, while an unsupported hard-close-only write cannot survive commit.

Migration 0035 and 0036 therefore enforce both implications at commit:

`retained snapshot => hard_closed authority`

`transition to hard_closed authority => retained snapshot`

The existing unique `(tenant_account_id, accounting_book_id, fiscal_period_id)` snapshot population identity supplies at most one retained counterpart. No second close writer, foreign billing truth, mutable session flag, cross-service SQL, or application-side status synthesis is introduced.

## Rejected alternatives

Application-only replay validation was rejected because it allows corrupt `hard_closed` authority to commit and pushes recovery onto the next reader. An immediate trigger was rejected because it would couple correctness to statement order and would conflict with the existing snapshot-first/status-second command transaction. Adding another snapshot writer or synthesizing retained evidence when the status changes was rejected because it would bypass close-package validation, journal-population freshness, exact currency/scope checks, and purpose-limited close authority.

## Recovery and operability

A pairing violation aborts the whole transaction. Operators must retry the supported close command from a clean transaction with the original immutable command identity; they must not patch the status or insert retained evidence independently. A failed migration 0036 transaction rolls back its trigger/function pair. Rollback of a successfully deployed migration must first prove that no consumer depends on bidirectional pairing and must not weaken existing retained-evidence immutability.

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html
