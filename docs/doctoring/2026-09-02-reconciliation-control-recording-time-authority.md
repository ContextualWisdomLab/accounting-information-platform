# Reconciliation control recording-time authority

Date: 2026-09-02

## Problem

`reconciliation_exception` and `reconciliation_evidence` were introduced with `recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()`. A default supplies a value only when the caller omits that column. Privileged direct SQL could therefore insert an arbitrary `recorded_at` and the later immutability guards would preserve the forged value. Exception resolution uses retained-evidence system time as part of temporal admission, so caller-shaped recording time is not acceptable provenance.

This defect is separate from business-valid time. `effective_at` describes when the exception or review evidence is valid for the accounting decision. `recorded_at` describes when AIS recorded that fact. Repairing the latter must not rewrite the former.

A second upgrade defect follows from the same PostgreSQL semantics. After a row has been stored under the pre-0024 schema, the database does not retain evidence of whether its `recorded_at` came from the column default or from an explicit caller value. Installing an INSERT trigger later cannot retroactively make that historical value database-owned. Treating all surviving pre-0024 rows as trusted system time would therefore manufacture provenance.

## Falsifiable RED

The insertion RED uses real PostgreSQL to insert an exception and a retained exception-resolution review artifact while explicitly supplying `recorded_at = 2100-01-01T00:00:00Z`. Before the repair, PostgreSQL returns and stores that caller value. The required post-0024 behavior is that the persisted value falls between database `clock_timestamp()` observations immediately before and after the insert. The same acceptance applies independently to both tables.

The upgrade RED installs through migration 0023 under a `NOSUPERUSER NOBYPASSRLS` migration owner, commits exception/evidence rows while explicit caller-shaped recording times are still accepted, and then attempts migration 0024. The upgrade must fail with `reconciliation_recording_time_legacy_preflight`; the transaction rollback must leave no temporary all-tenant visibility policy behind. A separate empty-database case must install 0024 successfully, remove both temporary policies, and retain only the two durable recording-time triggers.

These tests exercise PostgreSQL authority directly rather than application filtering or mocks.

## Decision

Append migration `0024_reconciliation_control_recording_time_authority.sql` with two boundaries.

First, before installing new authority, create transaction-scoped `FOR SELECT TO current_user USING (true)` policies on the two FORCE-RLS control tables and inspect their complete populations. If either table already contains rows, abort the migration. Pre-0024 rows have no row-level proof that their system time was database-generated, so the migration must not relabel them as trusted provenance. The failed transaction removes the temporary visibility policies automatically. Operational remediation must be explicit and auditable; deleting, rewriting, or silently backfilling immutable control evidence merely to satisfy the migration is not authorized by this change.

Second, after a clean preflight, drop the temporary visibility policies and install one row-level `BEFORE INSERT` trigger function that overwrites `NEW.recorded_at` with PostgreSQL `clock_timestamp()` for both `accounting_core.reconciliation_exception` and `accounting_core.reconciliation_evidence`.

The repair remains bounded:

- `effective_at` is unchanged and remains the business-valid-time fact;
- successful installation proves that no pre-trigger exception/evidence row survived into the stronger recording-time authority boundary;
- a populated pre-0024 store requires a separately reviewed remediation or migration strategy before retrying 0024;
- new reconciliation runs do not by themselves repair legacy rows because the preflight examines the retained historical tables, not only the active run;
- no posting, reversal, period-close, or accounting-policy authority is added;
- no cross-service access or foreign product state is introduced;
- existing immutable evidence and maker-checker controls remain intact.

The 0020–0024 stack is unreleased and is not present on protected `develop`. No released accounting history is reinterpreted by this forward repair. If a real deployment has nevertheless consumed the unreleased pre-0024 schema, upgrade must stop for explicit evidence remediation rather than silently promoting mutable-branch history to authoritative system time.

## Primary-source basis

PostgreSQL 18 documents that an omitted INSERT column receives its declared default, while an explicitly supplied expression is used for that column. A column default therefore does not establish non-overridable provenance. PostgreSQL also documents that a row-level `BEFORE` trigger may modify `NEW` before insertion and specifically identifies setting a current timestamp as a typical use. Those semantics support the post-0024 trigger boundary. PostgreSQL row-security semantics require the explicit migration-only visibility used by the all-tenant preflight because both source tables use FORCE RLS.

The accounting interpretation is deliberately limited. IFRS Accounting Standards do not prescribe this database mechanism. The control supports auditable evidence chronology and separation of valid time from AIS recording time; it is not a compliance or certification claim.

## Verification boundary

GREEN requires one unchanged exact head to pass the two real-PostgreSQL insertion regressions, the non-`BYPASSRLS` damaged/empty upgrade cases, the static migration contract, the complete Accounting Foundation behavior suite, exact owned statement/branch coverage, repository/public-docstring contracts, security/SAST/dependency review, reproducible package, SBOM and provenance gates. Queued or predecessor evidence is non-passing.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Default values*. https://www.postgresql.org/docs/18/ddl-default.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: INSERT*. https://www.postgresql.org/docs/18/sql-insert.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html
