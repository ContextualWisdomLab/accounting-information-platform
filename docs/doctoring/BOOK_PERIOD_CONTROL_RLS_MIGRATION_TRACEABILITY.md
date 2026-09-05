# Book-period control RLS migration traceability

## Scope

This note records installation, upgrade, and post-install master-data controls for the authoritative `accounting_book_period_control` and `period_journal_population_fence` relations and their `accounting_book` / `fiscal_period` seed sources. It does not grant a runtime tenant, change accounting policy, weaken posted-journal immutability, or transfer accounting authority to Billing or another source system.

## Migration-owner RLS finding

`accounting_core.current_tenant_account_id()` resolves a runtime tenant from `session_user`. A production schema/migration owner is not required to have such a runtime binding and should remain `NOSUPERUSER` / `NOBYPASSRLS`.

PostgreSQL 18 documents three distinct facts that matter here: ordinary table owners normally bypass row-level security; `FORCE ROW LEVEL SECURITY` makes the table owner subject to its policies; and `SUPERUSER` / `BYPASSRLS` are exceptional capabilities that bypass those policies. Therefore an all-tenant migration backfill cannot assume that the forced-RLS table owner can either read seed rows or write target rows when the policy derives a runtime tenant from an unbound `session_user`.

The first repair pass covered only target visibility. Fresh review then found the deeper source-side defect: migration 0005 had already forced `accounting_book` and `fiscal_period` through tenant RLS. Moving migration 0009's target backfill before FORCE on the newly created control table was still insufficient because the same unbound table owner could see no source books or periods. Likewise, migration 0034's owner-only window on the control/fence targets could not repair rows that its all-tenant `SELECT` could not see. Migration 0033's initial fence seed had already encoded the correct target-side principle by seeding before FORCE RLS, but it did not solve later source visibility.

There are no GitHub tags or releases for this repository at this repair point, so migration 0009 is not an immutable released artifact. The branch therefore repairs the unreleased migration order rather than adding a later migration that could not rescue an upgrade already failing at 0009.

## Post-install concurrent-seeding finding

Migration 0034 originally installed one `AFTER INSERT` trigger on `fiscal_period` and one on active `accounting_book`. Each trigger scanned the opposite population and inserted any missing book-period controls. That covered both sequential creation orders but did not cover the concurrent cross-product race.

Transaction A could insert a new active book and run its trigger while transaction B's new fiscal period was still uncommitted. Transaction B could independently insert that period and run its trigger while A's book was still uncommitted. Under MVCC neither scan is allowed to read the peer's uncommitted row. If both transactions then committed, the active `(tenant, book, period)` pair existed without its required control row or 64-row journal-population fence. The later journal guard would correctly fail closed, but a legitimate post-install book-period would be unusable until manually repaired. A fail-closed symptom is not a substitute for maintaining the authoritative master-data invariant.

A shared lock with no row-version change is also insufficient at fixed-snapshot isolation. PostgreSQL 18 Repeatable Read sees only data committed before the transaction snapshot and requires whole-transaction retry when an updater reaches a row that another transaction actually updated after that snapshot. Therefore the seeders need both ordering for Read Committed and a pre-existing MVCC version witness for Repeatable Read/Serializable.

## Post-install close-authority projection finding

`docs/DATA_MODEL.md` defines `accounting_book_period_control` as the authoritative close-state intersection for one tenant accounting book and fiscal period. The same document explicitly describes `fiscal_period.period_status_code` as an aggregate compatibility projection that must not be used to infer that every sibling book has the same close state.

Migration 0034 nevertheless copied `fiscal_period.period_status_code` and `fiscal_period.period_closed_at` into a control created for a later-inserted accounting book. That is an authority inversion: a new book could appear `soft_closed` or `hard_closed` even though no close command, maker-checker evidence, retained snapshot, close event, or book-specific chronology existed for that book. The same defect existed in the repair backfill for missing post-install pairs. A direct INSERT of a non-open fiscal period could also have projected that state across every active book.

This is not an IFRS interpretation. IFRS does not prescribe PostgreSQL aggregate ownership or row-level close-state derivation. It is a DDD/data-authority control required so AIS does not manufacture its own accounting evidence from a compatibility projection.

## Selected controls

Migration 0009 temporarily applies `NO FORCE ROW LEVEL SECURITY` to the already forced `accounting_book` and `fiscal_period` source tables, performs the existing all-tenant control-row seed while the new target table is not yet exposed through runtime policy, restores FORCE on both sources, and only then enables/forces RLS on `accounting_book_period_control`. This is a one-time conversion from the pre-book-scoped close model; the then-existing fiscal-period status is legacy source state being migrated and is not precedent for post-install projection copying.

Migration 0034 keeps RLS **enabled** everywhere. For its owner-only repair phase it applies `NO FORCE ROW LEVEL SECURITY` to all four participating relations: `accounting_book`, `fiscal_period`, `accounting_book_period_control`, and `period_journal_population_fence`. It performs the cross-tenant backfill, then restores `FORCE ROW LEVEL SECURITY` on every source and target before `COMMIT`. `NO FORCE` restores normal table-owner bypass; it does not disable RLS for non-owner runtime roles. The fence table participates because every inserted control synchronously invokes migration 0033's `SECURITY DEFINER` 64-stripe seeder.

Post-install automatic authority creation is now open-only. `seed_book_period_control_for_period()` returns without creating book controls when the inserted fiscal period is not `open`; an open period creates literal `open` controls with `period_closed_at = NULL`. `seed_book_period_control_for_book()` scans only shared fiscal periods whose compatibility projection is currently `open`, and again creates literal `open` controls with no close timestamp. Migration 0034's repair backfill fills only missing open pairs. Missing non-open book-period authority remains absent, causing journal/close admission to fail closed instead of inventing close history.

This deliberately does not define a reopen/adoption policy for a later-created book and an already non-open period. If that buyer use case is required, it needs an explicit book-period lifecycle command with authenticated capability, maker-checker separation where required, immutable command identity/evidence, and retained successor chronology. Copying `fiscal_period` state or using a manual SQL repair is not an acceptable substitute.

For post-install open book/period creation, both migration-0034 trigger functions update the same pre-existing `tenant_account` row before they scan the opposite master-data population. The statement sets `created_at` to its retained value, so it changes no tenant business fact, but it performs a real non-key row update and therefore provides the common MVCC version witness.

At PostgreSQL's default Read Committed isolation, a later updater waits for the competing tenant-row update and the following trigger statement receives a new command snapshot that can see the peer commit. At Repeatable Read/Serializable, a seeder whose fixed snapshot predates the competing tenant-row version fails with serialization error rather than committing a peer-blind pair; the complete master-data command must retry from a fresh transaction. Because `created_at` is not a key column, PostgreSQL uses the weaker `FOR NO KEY UPDATE` row-lock class for this update. PostgreSQL documents that `FOR NO KEY UPDATE` does not conflict with `FOR KEY SHARE`, preserving ordinary foreign-key checks by unrelated child inserts while still self-conflicting with the other master-data seeder.

The tenant-level write is intentionally limited to this low-frequency master-data boundary. It is not used by ordinary journal posting, does not contain a financial amount, and does not replace the per-book-period 64-stripe runtime freshness fence.

These `ALTER TABLE` operations and row updates remain transactional. Runtime traffic is not allowed to observe a committed half-state in which one of these relations permanently loses FORCE. The migration role must own all participating relations. Installation must not be made to work by granting `BYPASSRLS`, using a superuser as the normal deployment identity, assigning a fabricated runtime tenant to the migration role, treating `row_security=off` as a bypass, or executing `DISABLE ROW LEVEL SECURITY`.

## Alternatives considered

A trigger-side scan with no common lock was rejected because opposite-side inserts can each miss the other's uncommitted row and both commit.

A common `SELECT ... FOR NO KEY UPDATE` lock without changing a pre-existing row version was rejected as insufficient for Repeatable Read: after waiting, the transaction still has its original fixed snapshot and can remain unable to see the peer row.

A tenant-level `FOR UPDATE` fence was rejected as stronger than necessary because PostgreSQL documents that `FOR UPDATE` conflicts with `FOR KEY SHARE`, which would block unrelated foreign-key checks on the tenant row. The chosen non-key update obtains the weaker `FOR NO KEY UPDATE` class.

A table-level lock was rejected because it would serialize unrelated tenants and widen a low-frequency same-tenant master-data invariant into a global bottleneck.

A hash-based advisory lock was not selected because the canonical tenant row already provides a collision-free, pre-existing database identity and the fixed-snapshot case still needs a row-version change or an explicit serialization failure mechanism.

Copying `fiscal_period.period_status_code` or `period_closed_at` into a later-created book was rejected because the shared fields are compatibility state after book-scoped authority exists. This would manufacture closure without book-specific evidence. Initializing every historical/non-open pair as `open` was also rejected because that would silently reopen periods and admit backposting. The selected open-only materialization creates authority only where the shared period is already open and leaves non-open applicability fail closed.

## TDD and exact implementation evidence

- First static RED `800716a2b44370e41b0a5e65d86d4e30d1008765` required owner-safe target backfill without RLS disablement.
- Initial target-side repair: 0009 `92789123dca0a119e17df3f4b1d994c780f80264`; 0034 `a19be19b059834e04e965463230d56b3fa9c8aa7`.
- Fresh source-visibility RED `29808a77426e403d2c0277264ef6d2217f0e52d1` extends `tests/test_book_period_control_seed_contract.py` to require the owner-only window on `accounting_book` and `fiscal_period` as well as the control/fence targets.
- 0009 source-side repair `f5a28af32f70de66be5d702c5cf404b735546699` restores owner visibility on the forced-RLS seed sources and restores FORCE before target policy activation.
- 0034 full source/target repair `027fae479a7ae52b1db3119acd6549f50aa6dba2` surrounds the repair backfill with owner-only visibility on all four participating tables and restores FORCE on all four before commit.
- ADR 0006 alignment `4a87f3522e7a2f7f8dc1faa4cde6e0d6f3ebb3dd`; runtime-identity clarification `e040f3447700abfa5291237fa094c88019068e9f` makes an ordinary unbound `NOSUPERUSER`/`NOBYPASSRLS` migration owner distinct from runtime tenant and break-glass identities.
- Real-PostgreSQL runtime-state acceptance `cca0f5a7f9b1b4450933aea389e035863021503a` verifies through `pg_catalog.pg_class` that `accounting_book`, `fiscal_period`, `accounting_book_period_control`, and `period_journal_population_fence` all finish the installed migration chain with both RLS enabled and FORCE restored. This verifies the committed-state half of the owner-window contract; production-like execution as an unbound non-bypass owner remains separate release evidence.
- Concurrent cross-product RED `6cbcf0e334aab201a35cce2df1f2e887271cefad` adds a real PostgreSQL case where one transaction holds a newly inserted active book uncommitted while another creates the matching fiscal period. The period side must not commit before the book transaction resolves, and the final pair must own one control plus all 64 fences.
- Initial common-row lock repair `a038f8726ed0ef6f88a4ee7ea4920e6873e032f1` serializes the two trigger scans, with static ratchet `1d286cdd0d641b3723d90468a62cbe7da41a156f`.
- Fixed-snapshot RED `58b5f57b3bc7ae677544f90d0bfaf0c325045629` proves that a lock-only repair is not enough: a concurrent Repeatable Read period transaction must fail with PostgreSQL `SerializationFailure` and succeed only when the complete insert is retried from a fresh transaction.
- Fixed-snapshot causal repair `521104564345d096836da145140315f5e23fb1df` turns the tenant coordination point into a non-key UPDATE version witness without changing tenant business data; static contract `aa97d22b6f01b111ec3e701e4db229471276b807` pins that write before either peer scan and rejects a caller-shaped replacement timestamp.
- Book-state authority static RED `7592a3114f565f65c704b5b5f9f5eca39e420b75` rejects selecting shared period status/timestamp as values for a newly inserted book control.
- Real-PostgreSQL RED `083488b52f0ef4b9cf0fa21c87abea5159f9039f` requires a later-created active book to receive no control or fence population from a non-open shared period projection.
- Static authority ratchet `ac38a098c703d8ea72ba72fd1e7c9dc1a3814227` extends open-only derivation to new-period seeding and the repair backfill.
- Production repair `797cf556d8cf69e959b02cfd62cf4f30685e9658` implements open-only automatic control creation and repair backfill without changing migration 0009's one-time legacy conversion.
- ADR 0006 alignment is `1efd6e7f8c16caba1ff90129fe462759ca93aae7`; this traceability note is its normal fast-forward descendant.

These commits are development evidence only. The RED commits preceded their corresponding causal repairs, but no runner-observed RED or exact-head GREEN claim is valid until GitHub Actions executes the corresponding heads. The final release candidate still requires the real PostgreSQL migration chain with a production-like unbound non-bypass owner, tenant-isolation acceptance, security/SAST/dependency checks, migration/recovery evidence, independent review, and protected integration gates.

## Recovery and security effect

The SQL repairs are transactional. A failed migration must roll back every owner-force toggle and all inserted control/fence rows together. A runtime master-data serialization failure rolls back the book or period insert and its seed side effects together. Callers must retry the complete immutable master-data command from a fresh transaction; they must not fabricate book-period controls, delete posted journals, rewrite retained trial-balance evidence, or weaken tenant policies to make the operation appear successful.

The committed runtime state remains `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` on the tenant-scoped source and authority relations. This repair changes migration-owner visibility, master-data coordination, and post-install authority derivation only. It does not alter the runtime single-writer boundary, tenant policy expression, close command, maker-checker rules, journal amounts, or financial values.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: ALTER TABLE*. https://www.postgresql.org/docs/18/sql-altertable.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL 18 documentation: CREATE ROLE*. https://www.postgresql.org/docs/18/sql-createrole.html

PostgreSQL Global Development Group. (2026d). *PostgreSQL 18 documentation: Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html
