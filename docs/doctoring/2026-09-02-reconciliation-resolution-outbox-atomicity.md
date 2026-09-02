# Reconciliation exception-resolution outbox atomicity

**Date:** 2026-09-02  
**Owning boundary:** Reconciliation Review / durable exception-resolution command publication

## Problem

Migration 0020 already required an immutable exception-resolution command and the matching terminal exception status to commit together, but the accounting outbox event was still enforced only by the application write path. A privileged or defective SQL path could therefore commit command/status authority while omitting the integration event that downstream consumers use as the durable publication receipt.

That is an authority gap rather than a transport inconvenience: the repository promises that the named maker-checker command, terminal status, and matching accounting outbox evidence are one transactionally indivisible fact.

## Decision

Migration `0021_reconciliation_exception_resolution_outbox_pair.sql` adds a `DEFERRABLE INITIALLY DEFERRED` PostgreSQL constraint trigger on `accounting_core.reconciliation_exception_resolution_command`. At the deferred boundary it requires exactly one `accounting_integration.outbox_event` whose tenant, terminal-status-derived event type, exception aggregate reference, resolution-command payload reference, and database-assigned command hash all match the inserted immutable command.

The trigger is deliberately deferred because the supported application path inserts the immutable command first, then updates the exception status, then inserts the outbox event in the same transaction. PostgreSQL 18 documents that a deferred constraint trigger may execute at transaction end and that `SET CONSTRAINTS ... IMMEDIATE` can force pending checks earlier, which is the mechanism used by the real PostgreSQL regression.

The public migration loader now requires and applies 0021 after 0020; a missing 0021 fails before the existing foundation chain is applied. This change grants no journal posting, reversal, period-close, tax, account-selection, or accounting-policy authority.

## RED → GREEN evidence

`tests/test_reconciliation_exception_resolution_outbox_atomicity_red.py` was committed before the production repair. It writes the command and terminal status directly, intentionally omits the outbox event, forces the deferred boundary, and requires PostgreSQL to reject the transaction with no resolution side effects. `tests/test_migration_install_exception_resolution_outbox.py` additionally requires the exported loader to fail closed when 0021 is absent and to execute 0020 then 0021 in order.

Exact-head PostgreSQL, coverage, repository, security, supply-chain, and review gates remain the integration evidence boundary. A queued workflow is not passing evidence.

## References

PostgreSQL Global Development Group. (2026). *CREATE TRIGGER (PostgreSQL 18 documentation).* https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *SET CONSTRAINTS (PostgreSQL 18 documentation).* https://www.postgresql.org/docs/18/sql-set-constraints.html
