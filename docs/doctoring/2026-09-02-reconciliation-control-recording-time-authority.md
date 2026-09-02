# Reconciliation control recording-time authority

Date: 2026-09-02

## Problem

`reconciliation_exception` and `reconciliation_evidence` were introduced with `recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()`. A default supplies a value only when the caller omits that column. Privileged direct SQL could therefore insert an arbitrary `recorded_at` and the later immutability guards would preserve the forged value. Exception resolution uses retained-evidence system time as part of temporal admission, so caller-shaped recording time is not acceptable provenance.

This defect is separate from business-valid time. `effective_at` describes when the exception or review evidence is valid for the accounting decision. `recorded_at` describes when AIS recorded that fact. Repairing the latter must not rewrite the former.

## Falsifiable RED

Real PostgreSQL acceptance inserts an exception and a retained exception-resolution review artifact while explicitly supplying `recorded_at = 2100-01-01T00:00:00Z`. Before the repair, PostgreSQL returns and stores that caller value. The required behavior is that the persisted value falls between database `clock_timestamp()` observations immediately before and after the insert. The same acceptance is required independently for both tables.

The test does not assert application filtering or mock behavior. It exercises the database authority used by the maker-checker command.

## Decision

Append migration `0024_reconciliation_control_recording_time_authority.sql`. One row-level `BEFORE INSERT` trigger function overwrites `NEW.recorded_at` with PostgreSQL `clock_timestamp()` for both `accounting_core.reconciliation_exception` and `accounting_core.reconciliation_evidence`.

The repair is intentionally narrow:

- it does not change `effective_at`;
- it does not update existing rows;
- it does not create posting, reversal, period-close, or accounting-policy authority;
- it does not add cross-service access or foreign product state;
- it leaves the existing immutable evidence and maker-checker controls intact.

The current 0020–0024 stack is unreleased and is not present on protected `develop`, so the forward repair does not reinterpret a released accounting history. Any future operational upgrade from a released schema must treat already-recorded provenance as historical evidence and must not silently rewrite it.

## Primary-source basis

PostgreSQL documents that omitted INSERT columns receive their declared default, while explicitly supplied values are used by the INSERT. PostgreSQL also documents that a row-level `BEFORE` trigger can modify the `NEW` row and that the returned row becomes the inserted row. The trigger documentation specifically identifies populating a current timestamp as a typical `BEFORE`-trigger use. These semantics make a database-owned trigger materially stronger than a column default for system-time provenance.

The accounting interpretation is deliberately limited. IFRS Accounting Standards do not prescribe this database mechanism. The control supports auditable evidence chronology and separation of valid time from AIS recording time; it is not a compliance or certification claim.

## Verification boundary

GREEN requires one unchanged exact head to pass the two real-PostgreSQL recording-time regressions plus the complete Accounting Foundation behavior suite, exact owned statement/branch coverage, repository/public-docstring contracts, security/SAST/dependency review, reproducible package, SBOM and provenance gates. Queued or predecessor evidence is non-passing.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: INSERT*. https://www.postgresql.org/docs/18/sql-insert.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
