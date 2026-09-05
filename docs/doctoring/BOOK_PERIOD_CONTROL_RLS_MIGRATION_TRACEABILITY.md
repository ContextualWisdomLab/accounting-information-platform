# Book-period control RLS migration traceability

## Scope

This note records an installation and upgrade control for the authoritative `accounting_book_period_control` and `period_journal_population_fence` relations and their `accounting_book` / `fiscal_period` seed sources. It does not grant a runtime tenant, change accounting policy, weaken posted-journal immutability, or transfer accounting authority to Billing or another source system.

## Finding

`accounting_core.current_tenant_account_id()` resolves a runtime tenant from `session_user`. A production schema/migration owner is not required to have such a runtime binding and should remain `NOSUPERUSER` / `NOBYPASSRLS`.

PostgreSQL 18 documents three distinct facts that matter here: ordinary table owners normally bypass row-level security; `FORCE ROW LEVEL SECURITY` makes the table owner subject to its policies; and `SUPERUSER` / `BYPASSRLS` are exceptional capabilities that bypass those policies. Therefore an all-tenant migration backfill cannot assume that the forced-RLS table owner can either read seed rows or write target rows when the policy derives a runtime tenant from an unbound `session_user`.

The first repair pass covered only target visibility. Fresh review then found the deeper source-side defect: migration 0005 had already forced `accounting_book` and `fiscal_period` through tenant RLS. Moving migration 0009's target backfill before FORCE on the newly created control table was still insufficient because the same unbound table owner could see no source books or periods. Likewise, migration 0034's owner-only window on the control/fence targets could not repair rows that its all-tenant `SELECT` could not see. Migration 0033's initial fence seed had already encoded the correct target-side principle by seeding before FORCE RLS, but it did not solve later source visibility.

There are no GitHub tags or releases for this repository at this repair point, so migration 0009 is not an immutable released artifact. The branch therefore repairs the unreleased migration order rather than adding a later migration that could not rescue an upgrade already failing at 0009.

## Selected control

Migration 0009 temporarily applies `NO FORCE ROW LEVEL SECURITY` to the already forced `accounting_book` and `fiscal_period` source tables, performs the existing all-tenant control-row seed while the new target table is not yet exposed through runtime policy, restores FORCE on both sources, and only then enables/forces RLS on `accounting_book_period_control`.

Migration 0034 keeps RLS **enabled** everywhere. For its owner-only repair phase it applies `NO FORCE ROW LEVEL SECURITY` to all four participating relations: `accounting_book`, `fiscal_period`, `accounting_book_period_control`, and `period_journal_population_fence`. It performs the cross-tenant backfill, then restores `FORCE ROW LEVEL SECURITY` on every source and target before `COMMIT`. `NO FORCE` restores normal table-owner bypass; it does not disable RLS for non-owner runtime roles. The fence table participates because every inserted control synchronously invokes migration 0033's `SECURITY DEFINER` 64-stripe seeder.

These `ALTER TABLE` operations take PostgreSQL table locks inside the same migration transaction. Runtime traffic is not allowed to observe a committed half-state in which one of these relations permanently loses FORCE. The migration role must own all participating relations. Installation must not be made to work by granting `BYPASSRLS`, using a superuser as the normal deployment identity, assigning a fabricated runtime tenant to the migration role, treating `row_security=off` as a bypass, or executing `DISABLE ROW LEVEL SECURITY`.

## TDD and exact implementation evidence

- First static RED `800716a2b44370e41b0a5e65d86d4e30d1008765` required owner-safe target backfill without RLS disablement.
- Initial target-side repair: 0009 `92789123dca0a119e17df3f4b1d994c780f80264`; 0034 `a19be19b059834e04e965463230d56b3fa9c8aa7`.
- Fresh source-visibility RED `29808a77426e403d2c0277264ef6d2217f0e52d1` extends `tests/test_book_period_control_seed_contract.py` to require the owner-only window on `accounting_book` and `fiscal_period` as well as the control/fence targets.
- 0009 source-side repair `f5a28af32f70de66be5d702c5cf404b735546699` restores owner visibility on the forced-RLS seed sources and restores FORCE before target policy activation.
- 0034 full source/target repair `027fae479a7ae52b1db3119acd6549f50aa6dba2` surrounds the repair backfill with owner-only visibility on all four participating tables and restores FORCE on all four before commit.
- ADR 0006 alignment `4a87f3522e7a2f7f8dc1faa4cde6e0d6f3ebb3dd`; runtime-identity clarification `e040f3447700abfa5291237fa094c88019068e9f` makes an ordinary unbound `NOSUPERUSER`/`NOBYPASSRLS` migration owner distinct from runtime tenant and break-glass identities.
- Real-PostgreSQL runtime-state acceptance `cca0f5a7f9b1b4450933aea389e035863021503a` verifies through `pg_catalog.pg_class` that `accounting_book`, `fiscal_period`, `accounting_book_period_control`, and `period_journal_population_fence` all finish the installed migration chain with both RLS enabled and FORCE restored. This verifies the committed-state half of the owner-window contract; production-like execution as an unbound non-bypass owner remains separate release evidence.

These commits are development evidence only. Both static RED commits preceded their corresponding SQL repairs, but no runner-observed RED or exact-head GREEN claim is valid until GitHub Actions executes the corresponding heads. The final release candidate still requires the real PostgreSQL migration chain with a production-like unbound non-bypass owner, tenant-isolation acceptance, security/SAST/dependency checks, migration/recovery evidence, independent review, and protected integration gates.

## Recovery and security effect

Both SQL repairs are transactional. A failed migration must roll back every owner-force toggle and all inserted control/fence rows together. Operators must retry the complete migration after correcting the root cause; they must not fabricate book-period controls, delete posted journals, rewrite retained trial-balance evidence, or weaken tenant policies to make the migration appear successful.

The committed runtime state remains `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` on the tenant-scoped source and authority relations. This repair changes migration-owner visibility only and does not alter the runtime single-writer boundary, tenant policy expression, close authority, maker-checker rules, or financial values.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: ALTER TABLE*. https://www.postgresql.org/docs/18/sql-altertable.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: CREATE ROLE*. https://www.postgresql.org/docs/18/sql-createrole.html
