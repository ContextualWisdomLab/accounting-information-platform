# Reconciliation lifecycle capability privilege boundary

## Problem

Migration 0027 introduced `SECURITY DEFINER` helpers that acquire and release the tenant/run lifecycle session advisory lock. The lock is part of the freshness proof for the authoritative reconciliation transition: session lock acquisition must commit before a fresh `REPEATABLE READ` transaction derives run, review, exception, statement, journal-population, and book-to-bank evidence.

A database function is itself a privilege boundary. PostgreSQL grants `EXECUTE` on newly created functions and procedures to `PUBLIC` by default. A restricted runtime or reporting identity with schema `USAGE` could therefore invoke a newly created `SECURITY DEFINER` lifecycle helper unless the migration owner explicitly revokes that default. For an acquire helper, unintended invocation is also an availability risk because a caller can hold the tenant/run serialization key without possessing reconciliation business authority.

## Decision

The clean-install path revokes `PUBLIC EXECUTE` on both lifecycle session-lock helpers in the same migration-0027 transaction that creates them:

- `accounting_core.acquire_reconciliation_lifecycle_session(text, uuid)`
- `accounting_core.release_reconciliation_lifecycle_session(text, uuid)`

Migration `0028_reconciliation_lifecycle_capability_privileges.sql` repeats those revocations as a forward repair for a database that may already have applied a predecessor 0027. The canonical installer requires 0028. No generic runtime/read role receives an implicit lifecycle capability.

Issue #44 remains the owner for the eventual purpose-limited database capability. That later role must grant only the canonical named lifecycle execution surface after application authorization is stable. It must not grant raw INSERT on `reconciliation_run_transition_command`, generic UPDATE on `reconciliation_run`, or direct INSERT of reconciliation authority outbox events. Database capability membership remains independent of Keyverse/application authorization and tenant binding.

## Alternatives rejected

Leaving PostgreSQL defaults unchanged was rejected because schema access would silently imply invocation authority for a security-definer coordination primitive. Revoking only in migration 0028 was rejected for clean installs because it leaves a privilege window between separately committed migrations. Using `ALTER DEFAULT PRIVILEGES` as the only repair was rejected because it changes the migration owner's broader future-function policy rather than expressing the privilege contract at the two exact functions; it may be considered separately as deployment hardening.

## Executable evidence

`tests/test_reconciliation_lifecycle_session_lock_authority_contract.py` requires both creation-time and forward-upgrade revocation and requires the canonical installer to include 0028. `tests/test_postgres_runtime_rls.py` provisions a real tenant-bound login that is non-owner, non-superuser, and non-`BYPASSRLS`, grants the ordinary runtime schema/table surface, and requires PostgreSQL `InsufficientPrivilege` for both lifecycle helpers. Exact-head CI remains authoritative; queued or predecessor runs are non-passing.

This control does not grant or alter posting, reversal, period-close/open, tax, accounting-policy, or financial-reporting authority. The lifecycle lease remains coordination evidence, not an accounting fact.

## Primary references

National Institute of Standards and Technology. (2020, updated 2025). *Security and Privacy Controls for Information Systems and Organizations (NIST SP 800-53 Rev. 5), AC-6 Least Privilege*. U.S. Department of Commerce. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Privileges*. https://www.postgresql.org/docs/18/ddl-priv.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: ALTER DEFAULT PRIVILEGES*. https://www.postgresql.org/docs/18/sql-alterdefaultprivileges.html
