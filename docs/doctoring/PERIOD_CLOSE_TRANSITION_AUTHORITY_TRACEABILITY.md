# Period Close transition authority traceability

## Decision scope

This note traces two database-authority repairs on the Period Close path: transaction-isolation admission for book-period state transitions and tenant-bound runtime creation of the pre-existing journal-population fence. These are Accounting Information Platform implementation controls. They do not define IFRS accounting policy, grant Billing authority over accounting facts, or make PostgreSQL locking/RLS primitives accounting standards.

The authoritative accounting objects remain `accounting_book_period_control`, the immutable journal population, and retained close evidence. `fiscal_period` remains shared calendar/master data rather than a substitute book-level close authority.

## Problem and constraints

A close transition derives evidence under a transaction snapshot and then changes `accounting_book_period_control.period_status_code`. Migration 0033 already locks all 64 pre-existing `period_journal_population_fence` rows before a state change, but that mechanism depends on a snapshot isolation level that can detect a row version committed after the close snapshot. A raw `READ COMMITTED` state update can instead wait for the newest fence row and continue without the `40001` retry boundary expected by the supported close command.

Separately, runtime insertion of a new book-period control synchronously invokes `seed_period_journal_population_fence()`. The target fence table is `FORCE ROW LEVEL SECURITY`. Its policy is tenant-scoped through `accounting_core.current_tenant_account_id()`, which resolves the original `session_user` through `runtime_tenant_binding`. Runtime callers whose effective database role is actually subject to RLS therefore need a matching tenant binding before target DML. Migration 0034's one-time owner repair is intentionally different: it temporarily removes FORCE RLS while backfilling canonical open book-period intersections and restores FORCE RLS afterward.

`seed_period_journal_population_fence()` is `SECURITY DEFINER`, so PostgreSQL evaluates effective-role privileges with the function owner's `current_user`, while the tenant identity function deliberately derives its tenant from `session_user`. The explicit precondition must preserve PostgreSQL's own superuser/BYPASSRLS semantics; otherwise a diagnostic guard becomes stricter than the database operation it is meant to explain.

## Alternatives and decisions

| Concern | Selected control | Rejected alternative | Reason |
|---|---|---|---|
| Period transition isolation | Allow only `REPEATABLE READ` or `SERIALIZABLE` before any fence locking/state transition | Reject only the literal `read committed` string | PostgreSQL accepts `READ UNCOMMITTED` syntax but provides READ COMMITTED semantics; an allow-list fails closed for every weaker/unknown isolation level |
| Freshness witness | Preserve deterministic `FOR UPDATE` over all 64 pre-existing fence rows and the exact 64-row count check | Replace the fence with one shared revision row or remove it after adding the isolation check | Isolation and a pre-existing version witness solve different parts of the stale-close race; one shared row would reintroduce a posting hotspot |
| Runtime tenant precondition | When FORCE RLS is active and the effective function owner cannot bypass RLS, require `current_tenant_account_id()` to equal `NEW.tenant_account_id` before fence INSERT | Let an unbound RLS-subject runtime caller fall through to an opaque `WITH CHECK` failure | The explicit failure identifies the missing authority without weakening tenant isolation or synthesizing identity |
| Privileged/migration execution | Preserve superuser/BYPASSRLS effective-role behavior and migration 0034's temporary NO-FORCE owner backfill | Require runtime binding even when PostgreSQL itself would bypass RLS | A diagnostic precondition must not silently redefine PostgreSQL privilege semantics or break canonical install/test operators |
| Tenant identity | Continue using database-owned `runtime_tenant_binding`; do not revive caller-set tenant GUCs as authority | Mint an `app.tenant_account_id` or similar GUC inside the seeder | Caller-controlled context must not become accounting tenant authority |

## TDD and repair lineage

- `c5adb003590880730d5e67a528312a05f6ce15fb`: corrected the snapshot concurrency RED to acquire the actual tenant/book/period close advisory lock rather than a non-authoritative GUC.
- `a1d96d91546b82fef1593b3e8026dd5d301b169e`, `e91f783f7665acf7737c65e3dedee4431e3ecf0c`, `91ae0327379822c10421768546a7e722529b5978`: applied the same real snapshot-authority setup to currency, scope, and pre-close immutability REDs.
- `9aa4e02dbb7e27ba5beb943270da0c1b9dc8c113`: real PostgreSQL RED for a raw weak-isolation book-period state transition.
- `ab5cd11798d06f5b769dd41d23ea7e12e5cef42c`: first causal isolation repair, rejecting `READ COMMITTED` before fence locking.
- `4f146ad9ab67cf449acd3460b69e5a347d4dbcdd`: added the PostgreSQL `READ UNCOMMITTED` alias edge case, making the literal-only guard RED again.
- `0d0077bbec4afc9d54bb8b4838e5cf84dd9f4473`: replaced the blacklist with the fail-closed `REPEATABLE READ`/`SERIALIZABLE` allow-list.
- `23e6441e691c1e44c64aecac56f96fdf0bd93ecc`: static RED for an explicit runtime tenant-binding precondition before FORCE-RLS fence DML.
- `d1a294aa34b99bdcb71a4796c36dc74be6664502`: initial explicit binding guard.
- Source review then found that the initial guard was stricter than PostgreSQL for an effective superuser/BYPASSRLS `SECURITY DEFINER` owner. `bd1772bb06d380a3a623596e880105234cf7fb1c` and `66867a847ca499721f5a979251026a55359f7244` ratcheted the required effective-role distinction without rewriting history.
- `ec4d2c3583988b5bcb2458cd2d27f4050f2d1f0f`: current causal repair. It checks `pg_roles.rolsuper/rolbypassrls` for `current_user`, preserves PostgreSQL's effective-role RLS bypass semantics, and requires the `session_user` tenant binding only when the effective role is actually subject to FORCE RLS.

These SHAs are development lineage, not release evidence. A successor head does not inherit GREEN from a predecessor.

## Remaining acceptance and risk

The exact current descendant must still run the real PostgreSQL behavior suite, complete statement/branch coverage, security/SAST/dependency checks, and central required workflows. A queued or runner-less run is not GREEN.

This repair also does not close the separate historical-account-identity RED in `_post_closing_journal()`, the Reporting-package veto coupling on hard close, or the retained snapshot hash identity gap. Those findings remain independent and must not be hidden by declaring Period Close complete.

If test execution is later parallelized, any migration-upgrade test that temporarily removes a database trigger must move to a dedicated database or an explicitly serialized lane. The current canonical Accounting Foundation workflow executes `unittest discover` serially against a job-local PostgreSQL service, so that future condition is not currently satisfied.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html
