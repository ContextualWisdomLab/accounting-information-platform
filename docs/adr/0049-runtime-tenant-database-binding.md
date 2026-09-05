# ADR 0049: Bind runtime database logins to one tenant

**Status:** Accepted

## Context

Forced PostgreSQL row-level security is only a tenant boundary if the value used by each policy is itself trusted. A caller-writable custom GUC cannot be that authority: an ordinary application session can assign a custom two-part setting and, after credential compromise, could attempt to point the session at a different known tenant. PostgreSQL distinguishes `session_user`, the login that initiated the connection, from `current_user`, which can change under `SET ROLE` and while a `SECURITY DEFINER` function executes (PostgreSQL Global Development Group, 2026e). PostgreSQL also warns that security-definer functions must use a search path that excludes schemas writable by untrusted users (PostgreSQL Global Development Group, 2026f).

## Decision

`accounting_core.runtime_tenant_binding` is the database control-plane mapping from one authenticated runtime-role OID/name pair to one tenant. The mapping is admin-owned, effective-dated, and unavailable for direct runtime reads or writes. The active-role OID is retained alongside the role name so dropping and recreating a login with the same name does not silently inherit the former binding.

`accounting_core.current_tenant_account_id()` is a `STABLE SECURITY DEFINER` SQL function with `search_path = pg_catalog, accounting_core`. It takes no caller argument and derives the active tenant by joining the immutable session login (`session_user`) to the admin-owned binding and `pg_catalog.pg_roles`. Existing forced-RLS policies continue to call this function. The legacy `app.tenant_account_id` custom setting is no longer an authorization input; changing it cannot rebind a runtime session.

`PostgresPostingLedger` still receives an explicit tenant reference from the authenticated application boundary. It resolves that reference to the accounting tenant row and requires it to equal the database login binding. A bound mismatch or an unbound ordinary runtime identity fails closed before accounting work.

A normal schema/migration owner is an infrastructure identity, not a runtime tenant. It may remain unbound, `NOSUPERUSER`, and `NOBYPASSRLS`. Cross-tenant data-shape migrations must therefore arrange owner-safe migration ordering explicitly rather than borrowing a tenant identity or requiring standing RLS-bypass authority. For the book-period authority repair, migrations 0009/0034 seed while the table owner is not forced through the runtime tenant policy and restore committed `FORCE ROW LEVEL SECURITY` before runtime use; RLS is not disabled for non-owner roles. `SUPERUSER` or `BYPASSRLS` credentials are administrative break-glass/testing paths only, not a prerequisite or normal deployment credential.

Provisioning or rotating an application DB login therefore requires an owner-controlled insert of its current PostgreSQL role OID, role name, and tenant into `runtime_tenant_binding`. Tenant reassignment closes the old binding and creates a new one; runtime credentials never mutate this table.

## Consequences

A compromised ordinary database credential cannot cross tenant scope merely by changing a request field, a session GUC, or `SET ROLE`. The database credential itself becomes purpose- and tenant-bound, complementing rather than replacing HTTP/OIDC authorization. Operators must provision the binding before switching traffic to a new runtime login, and backup/restore or role recreation must re-establish the current role OID deliberately. Migration tooling must preserve table ownership and the documented owner-only RLS transition when cross-tenant upgrade backfill is required; a failure is repaired by retrying the migration, not by granting runtime tenant authority or weakening the committed policy. This is defense-in-depth evidence readiness, not a claim of SOC 2, CSAP, or jurisdictional certification.

## References

PostgreSQL Global Development Group. (2026e). *PostgreSQL 18 documentation: System information functions and operators*. https://www.postgresql.org/docs/18/functions-info.html

PostgreSQL Global Development Group. (2026f). *PostgreSQL 18 documentation: Function security*. https://www.postgresql.org/docs/18/perm-functions.html

PostgreSQL Global Development Group. (2026g). *PostgreSQL 18 documentation: CREATE POLICY*. https://www.postgresql.org/docs/18/sql-createpolicy.html

PostgreSQL Global Development Group. (2026h). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html
