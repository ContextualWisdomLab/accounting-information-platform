# Book-period control RLS migration traceability

## Scope

This note records an installation and upgrade control for the authoritative `accounting_book_period_control` and `period_journal_population_fence` relations. It does not grant a runtime tenant, change accounting policy, weaken posted-journal immutability, or transfer accounting authority to Billing or another source system.

## Finding

`accounting_core.current_tenant_account_id()` resolves a runtime tenant from `session_user`. A production schema/migration owner is not required to have such a runtime binding and should remain `NOSUPERUSER` / `NOBYPASSRLS`.

PostgreSQL 18 documents three distinct facts that matter here: ordinary table owners normally bypass row-level security; `FORCE ROW LEVEL SECURITY` makes the table owner subject to its policies; and `SUPERUSER` / `BYPASSRLS` are exceptional capabilities that bypass those policies. Therefore an all-tenant migration backfill cannot assume that the forced-RLS table owner can insert rows selected across tenants when the policy is `tenant_account_id = accounting_core.current_tenant_account_id()` and the migration login has no runtime tenant binding.

The defect existed in two development-only paths. Migration 0009 enabled and forced tenant RLS before its upgrade backfill of existing accounting books × fiscal periods. Migration 0034 also attempted a cross-tenant repair after both `accounting_book_period_control` and the 64-stripe `period_journal_population_fence` were already FORCE RLS protected. Migration 0033 had already encoded the correct principle for its initial fence population by seeding before FORCE RLS.

There are no GitHub tags or releases for this repository at this repair point, so migration 0009 is not an immutable released artifact. The branch therefore repairs the unreleased migration order rather than adding an ineffective later migration that a failing 0009 upgrade could never reach.

## Selected control

Migration 0009 now performs its existing all-tenant control-row backfill before enabling and forcing RLS. Runtime access is not granted inside that migration transaction, so there is no committed runtime interval without the intended forced-RLS policy.

Migration 0034 keeps RLS **enabled** on both target tables. For its owner-only repair phase it executes `NO FORCE ROW LEVEL SECURITY` on `accounting_book_period_control` and `period_journal_population_fence`, performs the cross-tenant backfill, then restores `FORCE ROW LEVEL SECURITY` on both tables before `COMMIT`. `NO FORCE` restores the normal table-owner bypass; it does not disable RLS for non-owner runtime roles. The fence table must participate in the same owner window because every inserted control synchronously invokes migration 0033's `SECURITY DEFINER` 64-stripe seeder.

The migration role must own these tables. Installation must not be made to work by granting `BYPASSRLS`, using a superuser as the normal deployment identity, assigning a fabricated runtime tenant to the migration role, setting `row_security=off` as a bypass, or executing `DISABLE ROW LEVEL SECURITY`.

## TDD and exact implementation evidence

- Test-first static RED: `800716a2b44370e41b0a5e65d86d4e30d1008765`, `tests/test_book_period_control_seed_contract.py`. It requires the 0009 backfill to precede FORCE RLS, requires 0034's owner-only `NO FORCE` window to surround its repair backfill on both forced-RLS tables, and forbids `DISABLE ROW LEVEL SECURITY`.
- Initial migration-order repair: `92789123dca0a119e17df3f4b1d994c780f80264`, `database/migrations/0009_accounting_book_period_control.sql`.
- Post-install repair: `a19be19b059834e04e965463230d56b3fa9c8aa7`, `database/migrations/0034_book_period_control_seed.sql`.
- ADR alignment: `dc06c5cbe6c9d3dba03b8fb07901838611e673fa`, `docs/adr/0006-fiscal-period-close-snapshot.md`.

These commits are development evidence only. The RED commit was created before the SQL repair, but no claim of runner-observed RED or exact-head GREEN is valid until GitHub Actions executes the corresponding heads. The final release candidate still requires the real PostgreSQL migration chain, tenant isolation, security/SAST/dependency checks, migration/recovery evidence, independent review, and protected integration gates.

## Recovery and security effect

Both SQL repairs are transactional. A failed migration must roll back the owner-force toggle and all inserted control/fence rows together. Operators must retry the complete migration after correcting the root cause; they must not fabricate book-period controls, delete posted journals, rewrite retained trial-balance evidence, or weaken tenant policies to make the migration appear successful.

The committed runtime state remains `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` for both authority relations. This repair changes migration-owner behavior only and does not alter the runtime single-writer boundary, tenant policy expression, close authority, maker-checker rules, or financial values.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: ALTER TABLE*. https://www.postgresql.org/docs/18/sql-altertable.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: CREATE ROLE*. https://www.postgresql.org/docs/18/sql-createrole.html
