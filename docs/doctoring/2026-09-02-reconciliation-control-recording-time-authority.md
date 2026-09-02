# Reconciliation control recording-time authority

Date: 2026-09-02

## Problem

`reconciliation_exception` and `reconciliation_evidence` were introduced with `recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()`. A default supplies a value only when the caller omits that column. Privileged direct SQL could therefore insert an arbitrary `recorded_at` and the later immutability guards would preserve the forged value. Exception resolution uses retained-evidence system time as part of temporal admission, so caller-shaped recording time is not acceptable provenance.

This defect is separate from business-valid time. `effective_at` describes when the exception or review evidence is valid for the accounting decision. `recorded_at` describes when AIS recorded that fact. Repairing the latter must not rewrite the former.

A second upgrade defect follows from the same PostgreSQL semantics. After a row has been stored under the pre-0024 schema, the database does not retain evidence of whether its `recorded_at` came from the column default or from an explicit caller value. Installing an INSERT trigger later cannot retroactively make that historical value database-owned. Treating all surviving pre-0024 rows as trusted system time would therefore manufacture provenance.

The first repair attempted to fail migration 0024 whenever either historical table was populated. That avoided false provenance but created an operational dead end: migration 0020 had already made retained reconciliation evidence immutable, and the governing accounting contract forbids deleting or rewriting historical audit evidence merely to satisfy a migration. A real populated deployment therefore had no repository-supported upgrade path. “Stop and perform an audited remediation” was not sufficient because the repository contained no safe remediation capable of proving whether an old timestamp had been caller-supplied.

## Falsifiable RED

The insertion RED uses real PostgreSQL to insert exception and retained exception-resolution review rows while explicitly supplying forged `recorded_at` values. Before migration 0024, PostgreSQL stores those caller values.

The upgrade RED now requires a stronger and commercially usable property: a non-`BYPASSRLS` migration owner must be able to install 0024 over populated pre-0024 control history **without changing either historical timestamp**. Existing rows must become explicitly `legacy_unverified`; they remain audit evidence but are not promoted to database-owned chronology. Rows inserted after 0024 must receive `recording_time_authority_code = 'database_clock'` and a PostgreSQL-assigned `recorded_at` even when the caller supplies both fields. Recording-time provenance must be immutable after insertion, and a maker-checker resolution command must refuse legacy exception/review rows whose system-time authority is not `database_clock`.

A separate empty-database case requires the same columns, insertion guards, immutability guards, and resolution-command admission guard. These tests exercise PostgreSQL authority directly rather than application filtering or mocks.

## Decision

Migration `0024_reconciliation_control_recording_time_authority.sql` uses explicit provenance classification rather than destructive migration gating.

1. Add `recording_time_authority_code` to both source tables with the constant historical value `legacy_unverified`. Existing rows keep their original `recorded_at` exactly. PostgreSQL 18 documents that adding a column with a constant default can be satisfied from table metadata without rewriting every row; this is preferable to a volatile timestamp backfill that would both rewrite data and invent historical system time.
2. Drop the column defaults immediately. The durable INSERT trigger, not a caller-visible default, owns the new-row value.
3. The shared `BEFORE INSERT` trigger overwrites both `recorded_at` with `clock_timestamp()` and `recording_time_authority_code` with `database_clock`.
4. Dedicated UPDATE guards make both the timestamp and its authority marker immutable. This is explicit on `reconciliation_exception`; retained `reconciliation_evidence` also remains protected by its broader immutability control.
5. A later `BEFORE INSERT` guard on `reconciliation_exception_resolution_command` requires the exact exception and retained review artifact selected by the existing command-hash trigger to have `database_clock` authority. PostgreSQL fires same-kind triggers in alphabetical trigger-name order, so the existing hash guard resolves `NEW.reconciliation_evidence_id` before the recording-time authority guard checks it.

This preserves the complete legacy population and makes the authority distinction queryable instead of pretending that unprovable history can be repaired after the fact. An old open exception may remain available to auditors, but it cannot be newly terminalized through the maker-checker command using untrusted system chronology. Operational work proceeds by opening a new reconciliation run after 0024 and recording new review evidence under database-owned time; the legacy run is preserved rather than rewritten.

The repair remains bounded:

- `effective_at` is unchanged and remains the business-valid-time fact;
- legacy `recorded_at` values are neither changed nor trusted;
- new rows are tagged `database_clock` only by PostgreSQL;
- command admission rejects `legacy_unverified` exception/review chronology;
- no posting, reversal, period-close, or accounting-policy authority is added;
- no cross-service access or foreign product state is introduced;
- existing immutable evidence, outbox retention, and maker-checker controls remain intact.

The 0020–0024 stack is unreleased and is not present on protected `develop`; this forward repair changes only the current mutable stack. No released accounting history is reinterpreted.

## Alternatives considered

**Abort 0024 on any pre-existing control row.** Rejected after RED review. It avoids false provenance but gives any populated deployment no repository-supported path forward while the same product contract forbids deleting or rewriting immutable audit evidence.

**Backfill old rows with the migration timestamp.** Rejected because the migration time is not the historical AIS recording time and would manufacture chronology.

**Assume old defaults were trustworthy.** Rejected because PostgreSQL permits explicit values to override a column default and the old rows retain no proof of how the value was produced.

**Store a single global “legacy database upgraded” flag.** Rejected because recording-time authority is a property of each retained row and must travel with the exact evidence used by a command.

**Preserve row-level provenance and gate only commands that require trusted chronology.** Selected. It preserves audit history, permits an online forward schema transition, fails closed at the authority boundary, and makes the legacy/new distinction inspectable by operators and auditors.

## Primary-source basis

PostgreSQL 18 documents that a constant default added with `ALTER TABLE ... ADD COLUMN` can be represented in metadata rather than rewriting each existing row, while a volatile default such as `clock_timestamp()` requires per-row evaluation. That is why the migration uses a constant `legacy_unverified` marker and never backfills old timestamps. PostgreSQL also documents that row-level `BEFORE` triggers may modify `NEW` before insertion, and that multiple triggers of the same kind fire alphabetically by trigger name, which supports the command admission ordering used by migration 0024. citeturn135631search0turn495404search0

The accounting interpretation is deliberately limited. IFRS Accounting Standards do not prescribe this database mechanism. The control supports auditable evidence chronology and separation of valid time from AIS recording time; it is not a compliance or certification claim.

## Verification boundary

GREEN requires one unchanged exact head to pass the real-PostgreSQL forged-time insertion regressions, populated and empty non-`BYPASSRLS` upgrade cases, legacy-authority command rejection, immutable-authority mutation checks, the static migration contract, complete Accounting Foundation behavior suite, exact owned statement/branch coverage, repository/public-docstring contracts, security/SAST/dependency review, reproducible package, SBOM and provenance gates. Queued or predecessor evidence is non-passing.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Modifying tables*. https://www.postgresql.org/docs/18/ddl-alter.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Default values*. https://www.postgresql.org/docs/18/ddl-default.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: INSERT*. https://www.postgresql.org/docs/18/sql-insert.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
