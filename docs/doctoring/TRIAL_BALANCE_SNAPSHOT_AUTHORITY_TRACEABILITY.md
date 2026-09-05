# Trial-balance snapshot authority traceability

## Problem

A retained `trial_balance_snapshot` is Period Close evidence. Before this repair, the final migration-0033 snapshot guard admitted a direct SQL insert in a `soft_closed` book-period when the session had `accounting_closing_writer` membership and set the caller-controlled `accounting_core.journal_write_role` custom setting to `period_closing`, even when the canonical tenant/book/period close advisory lock was absent. That made mutable session context sufficient to create retained close evidence outside the governed hard-close command.

This is an Accounting Information Platform authority defect, not an IFRS requirement. IFRS does not prescribe PostgreSQL advisory-lock or session-setting mechanics.

## Constraints

- `trial_balance_snapshot` remains owned by the Period Close bounded context.
- A caller-controlled setting may describe journal-admission context but cannot mint retained close authority.
- Direct `open -> hard_closed` remains supported.
- A zero-net-income hard close may create a snapshot without emitting a period-closing journal, so snapshot authority cannot depend on a journal side effect.
- The database capability and exact tenant/book/period close lock are both required; neither replaces tenant scope, immutable source derivation, idempotency, snapshot immutability, or the journal-population freshness fences.
- No posting, Billing, Reporting, LLM, or generic runtime path gains snapshot authority.

## Alternatives considered

1. Keep `journal_write_role = period_closing` as an alternate proof. Rejected because PostgreSQL session settings are mutable session state. PostgreSQL documents `set_config` as the SQL-level mechanism for changing run-time settings, so the value is not an unforgeable command receipt.
2. Require a period-closing journal row before snapshot insertion. Rejected because a valid zero-net-income hard close may need no closing journal.
3. Require the exact close advisory lock plus `accounting_closing_writer`. Selected because the canonical hard-close command already holds that lock before deriving retained evidence, including the zero-closing-journal case, and the database can verify the lock on the current backend and exact two-key identity through `pg_locks`.

## Selected control

Migration 0030 now requires all of the following for its `soft_closed` snapshot admission boundary:

- authoritative `accounting_book_period_control.period_status_code = 'soft_closed'`;
- `pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')`;
- the exact tenant/book/period exclusive advisory lock held by `pg_backend_pid()`.

Migration 0033 replaces that guard for the final supported chain and applies the same lock requirement to both `open` and `soft_closed` hard-close snapshot creation. Its snapshot guard no longer reads `accounting_core.journal_write_role`.

The journal-admission guard still uses `journal_write_role` to distinguish purpose-limited soft-close journal kinds. That is a separate control: retained snapshot authority no longer treats the setting as proof of a hard-close command.

## RED -> repair evidence

- RED `ef2f9dc5dd0644826905f053c0a791579b9c4000`: replaces the former direct-GUC chronology probe with a real PostgreSQL regression that sets `accounting_core.journal_write_role = period_closing` and requires the forged snapshot insert to fail with `trial_balance_snapshot_authority_required`.
- Final-chain repair `e2a01fc98737b767b2cab3db80d8cf69b09b48af`: migration 0033 requires the exact close lock for both open and soft-closed snapshot admission and removes GUC reliance from the snapshot guard.
- Upgrade-window repair `b790b5d10e870d770d48ccb93485192c11d9466b`: migration 0030 also requires the exact close lock, so a database between 0030 and 0033 does not expose the earlier GUC-only snapshot path.
- Static ratchet `a677226426d4daff6f4b3dbcb6110eabe050f6c7`: repository contracts require both snapshot-guard versions to remain lock-bound and reject reintroduction of `journal_write_role_value` into snapshot authority.

These commits are development lineage only. They are not release evidence until one unchanged exact head passes the real PostgreSQL suite, current security/SAST/dependency gates, required review, stack integration, and release controls.

## Verification and recovery

The governed hard-close regressions must still prove that:

- ordinary and zero-closing-journal hard close each create exactly one retained snapshot;
- GUC-only direct SQL cannot create a snapshot;
- a missing or wrong advisory lock fails before retained evidence persists;
- failed close transactions leave no snapshot, closing journal, close event, or hard-close transition;
- retries start from a fresh transaction with the same immutable command/source identity.

If lock-identity logic changes, update the application lock acquisition, both migration guards, PostgreSQL acceptance tests, ADR 0006, and this trace together. Do not recover by manually inserting or relabeling retained snapshots.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: System administration functions*. https://www.postgresql.org/docs/18/functions-admin.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: pg_locks*. https://www.postgresql.org/docs/18/view-pg-locks.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Setting parameters*. https://www.postgresql.org/docs/18/config-setting.html
