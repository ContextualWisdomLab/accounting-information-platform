# Reconciliation authority retention preflight under FORCE RLS — 2026-09-02

## Problem

Migration `0022_reconciliation_authority_outbox_retention.sql` is an upgrade damage check as well as a forward control installation. Before installing its durable retention triggers, it must prove that every immutable reconciliation exception-resolution command and every reconciliation-run transition command still has exactly one matching accounting outbox event.

The three tables used by that check are tenant-scoped authority/evidence tables protected by row-level security. A migration identity that does not bypass RLS and has no tenant context can therefore observe an empty population even when the database contains commands or outbox rows. In that state the preflight would be vacuous: a database with a missing retained authority event could be accepted because the migration query could not see the damaged command.

This is distinct from migration 0023's orphan-event check. Migration 0022 detects an immutable command whose required event is missing or duplicated. Migration 0023 detects a reserved reconciliation authority event whose claimed immutable command does not exist. Both directions are required.

## Decision

Migration 0022 creates three temporary `FOR SELECT TO current_user USING (true)` policies, limited to:

- `accounting_core.reconciliation_exception_resolution_command`;
- `accounting_core.reconciliation_run_transition_command`; and
- `accounting_integration.outbox_event`.

The policies are created before the all-tenant damage preflight and dropped immediately after it, before the durable retention functions and constraint triggers are installed. They exist only inside the migration transaction. Runtime FORCE RLS policy, tenant isolation, journal/posting authority, period-close authority and application credentials are unchanged.

The migration does not grant `BYPASSRLS`, require a superuser application identity, synthesize a missing event or grandfather damaged provenance. If the preflight finds zero or more than one matching event for an immutable command, the transaction fails and operators must restore verified provenance before retrying the migration.

## Alternatives rejected

**Assume the migration owner bypasses RLS.** Rejected because repository operation and migration correctness must not depend on an implicit superuser/BYPASSRLS deployment convention.

**Bind one tenant before the preflight.** Rejected because the migration must evaluate all retained authority commands, not only one tenant's rows.

**Rely on migration 0023.** Rejected because orphan-event admission checks the inverse relationship and cannot detect a command whose required event disappeared.

**Disable FORCE RLS for the migration.** Rejected because that changes a durable tenant-isolation control rather than granting the narrow transaction-local read visibility required by the upgrade check.

## Executable acceptance

`tests/test_reconciliation_outbox_retention_migration_contract.py` requires all three temporary policies, requires them to precede the damage preflight, and requires their removal before the durable retention guard is installed. Real PostgreSQL upgrade acceptance must additionally prove that a non-`BYPASSRLS` migration identity detects a deliberately damaged pre-0022 command/event pair, that a healthy upgrade succeeds, and that no temporary visibility policy remains after successful installation or rollback.

The static migration contract is repository evidence only. It does not substitute for the real PostgreSQL acceptance lane, exact owned statement/branch coverage, security/dependency evidence, review, package/SBOM/provenance or protected integration evidence.

## Traceability

- Migration: `database/migrations/0022_reconciliation_authority_outbox_retention.sql`
- Repository contract: `tests/test_reconciliation_outbox_retention_migration_contract.py`
- Retention behavior: `tests/test_reconciliation_outbox_retention_postgres.py`
- Orphan-event admission: `database/migrations/0023_reconciliation_authority_outbox_orphan_guard.sql`
- Operability boundary: `docs/OPERABILITY.md`
- Related retention decision: `docs/doctoring/2026-09-02-reconciliation-outbox-retention-authority.md`

Primary database semantics are governed by the PostgreSQL 18 row-security documentation already recorded in `docs/doctoring/REFERENCES.md`. This note is an implementation/control trace, not a compliance or certification claim.
