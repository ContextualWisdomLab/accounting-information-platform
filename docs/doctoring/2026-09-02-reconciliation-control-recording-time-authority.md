# Reconciliation control recording-time authority

Date: 2026-09-02

## Problem

`reconciliation_exception` and `reconciliation_evidence` were introduced with `recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()`. A default supplies a value only when the caller omits that column. Privileged direct SQL could therefore insert an arbitrary `recorded_at` and the later immutability guards would preserve the forged value. Exception resolution uses retained-evidence system time as part of temporal admission, so caller-shaped recording time is not acceptable provenance.

This defect is separate from business-valid time. `effective_at` describes when the exception or review evidence is valid for the accounting decision. `recorded_at` describes when AIS recorded that fact. Repairing the latter must not rewrite the former.

A second upgrade defect follows from the same PostgreSQL semantics. After a row has been stored under the pre-0024 schema, the database does not retain evidence of whether its `recorded_at` came from the column default or from an explicit caller value. Installing an INSERT trigger later cannot retroactively make that historical value database-owned. Treating all surviving pre-0024 rows as trusted system time would therefore manufacture provenance.

There are two materially different legacy populations. Unresolved exception and review-evidence rows are retained audit inputs; they can survive the upgrade as `legacy_unverified` because post-0024 command admission refuses to turn those rows into new maker-checker authority. A resolution command already committed under migrations 0020–0023 is different: it has already terminalized an exception and can be composed into a later lifecycle snapshot. If 0024 silently labels its source rows `legacy_unverified` but leaves the command admissible for later finalization, caller-shaped historical source chronology can still become new `reconciled` authority after the upgrade.

The first repair attempted to fail migration 0024 whenever either historical source table was populated. That avoided false provenance but created an operational dead end: migration 0020 had already made retained reconciliation evidence immutable, and the governing accounting contract forbids deleting or rewriting historical audit evidence merely to satisfy a migration. The repaired boundary therefore preserves unresolved source rows non-destructively but fails closed when authority-bearing pre-0024 resolution commands already exist.

## Falsifiable RED

The insertion RED uses real PostgreSQL to insert exception and retained exception-resolution review rows while explicitly supplying forged `recorded_at` values. Before migration 0024, PostgreSQL stores those caller values.

The unresolved-history upgrade case requires a non-`BYPASSRLS` migration owner to install 0024 over populated pre-0024 exception/review evidence **without changing either historical timestamp**. Existing rows become explicitly `legacy_unverified`; they remain audit evidence but are not promoted to database-owned chronology. Rows inserted after 0024 receive `recording_time_authority_code = 'database_clock'` and a PostgreSQL-assigned `recorded_at` even when the caller supplies both fields. Recording-time provenance is immutable after insertion, and a new maker-checker resolution command refuses legacy exception/review rows whose system-time authority is not `database_clock`.

The authority-bearing upgrade RED is separate. Install through 0023, create pre-0024 exception and retained review rows with caller-shaped `recorded_at`, commit a valid resolution command, then execute migration 0024 as a `NOSUPERUSER NOBYPASSRLS` migration owner. The migration must fail with `reconciliation_resolution_legacy_recording_time_preflight` before either `recording_time_authority_code` column is added. The failed transaction must also leave no temporary upgrade-visibility policy behind. This prevents an already-authoritative command from laundering unverifiable source chronology into a later lifecycle transition.

A separate empty-database case requires the same columns, insertion guards, immutability guards, and resolution-command admission guard. These tests exercise PostgreSQL authority directly rather than application filtering or mocks.

## Decision

Migration `0024_reconciliation_control_recording_time_authority.sql` combines a narrow authority preflight with explicit row-level provenance classification.

1. Before durable schema changes, create a transaction-scoped `FOR SELECT TO current_user USING (true)` policy on forced-RLS `reconciliation_exception_resolution_command`. If any pre-0024 resolution command exists, abort with `reconciliation_resolution_legacy_recording_time_preflight`. Drop the temporary policy on the healthy path; rollback removes it on failure.
2. Do **not** reject unresolved exception/review-evidence rows merely because they exist. Add `recording_time_authority_code` to both source tables with constant historical value `legacy_unverified`; existing rows keep their original `recorded_at` exactly. PostgreSQL 18 documents that a column with a constant default can be represented from table metadata without rewriting every historical row.
3. Drop the marker defaults immediately. The durable INSERT trigger, not a caller-visible default, owns new-row provenance.
4. The shared `BEFORE INSERT` trigger overwrites both `recorded_at` with `clock_timestamp()` and `recording_time_authority_code` with `database_clock`.
5. Dedicated UPDATE guards make both the timestamp and authority marker immutable. Retained `reconciliation_evidence` also remains covered by its broader append-only control.
6. A later `BEFORE INSERT` guard on `reconciliation_exception_resolution_command` requires the exact exception and retained review artifact selected by the existing command-hash trigger to have `database_clock` authority. PostgreSQL fires same-kind triggers in alphabetical trigger-name order, so the existing hash guard resolves `NEW.reconciliation_evidence_id` before the recording-time authority guard checks it.

This preserves unresolved legacy history and makes its uncertainty queryable while preventing an already-authoritative pre-0024 resolution command from crossing the upgrade boundary silently. An old open exception remains available to auditors but cannot be newly terminalized from untrusted system chronology. A pre-0024 resolution command requires an explicitly reviewed audited remediation or retention of the prior operational stack; deleting or rewriting the immutable command/status/outbox triplet or inventing database-clock provenance is not an acceptable migration shortcut.

The repair remains bounded:

- `effective_at` is unchanged and remains the business-valid-time fact;
- unresolved legacy `recorded_at` values are neither changed nor trusted;
- new rows are tagged `database_clock` only by PostgreSQL;
- new command admission rejects `legacy_unverified` exception/review chronology;
- pre-existing resolution authority blocks upgrade before durable 0024 schema changes;
- no posting, reversal, period-close, or accounting-policy authority is added;
- no cross-service access or foreign product state is introduced;
- existing immutable evidence, outbox retention, and maker-checker controls remain intact.

The 0020–0024 stack is unreleased and is not present on protected `develop`; this forward repair changes only the current mutable stack. No released accounting history is reinterpreted.

## Alternatives considered

**Abort 0024 on any pre-existing control row.** Rejected. It avoids false provenance but gives any populated deployment no repository-supported path forward while the same product contract forbids deleting or rewriting immutable audit evidence.

**Allow pre-existing resolution commands and mark only their source rows `legacy_unverified`.** Rejected. The immutable command can still be composed into a later lifecycle transition, so this would preserve authority while explicitly admitting that the chronology supporting it is unverified.

**Backfill old rows with the migration timestamp.** Rejected because the migration time is not the historical AIS recording time and would manufacture chronology.

**Assume old defaults were trustworthy.** Rejected because PostgreSQL permits explicit values to override a column default and old rows retain no proof of how the value was produced.

**Preserve unresolved row-level provenance while gating authority-bearing legacy commands.** Selected. It preserves audit history, permits a non-destructive forward transition for unresolved evidence, and fails closed exactly where unverifiable chronology has already become authority.

## Primary-source basis

PostgreSQL 18 documents that a constant default added with `ALTER TABLE ... ADD COLUMN` can be represented in metadata rather than rewriting each existing row, while a volatile default such as `clock_timestamp()` requires per-row evaluation. That is why the migration uses a constant `legacy_unverified` marker and never backfills old timestamps. PostgreSQL also documents row-level `BEFORE` trigger mutation of `NEW`, deterministic same-kind trigger ordering by trigger name, row security policies, and transactional DDL; those properties support the admission guard and rollback-safe migration preflight.

The accounting interpretation is deliberately limited. IFRS Accounting Standards do not prescribe this database mechanism. The control supports auditable evidence chronology and separation of valid time from AIS recording time; it is not a compliance or certification claim.

## Verification boundary

GREEN requires one unchanged exact head to pass the static preflight-order contract, the real-PostgreSQL authority-bearing pre-0024 resolution-command rejection under a non-`BYPASSRLS` migration owner, rollback proof for authority columns and the temporary policy, populated unresolved-history preservation, empty upgrade, forged-time insertion regressions, legacy-authority command rejection, immutable-authority mutation checks, the complete Accounting Foundation behavior suite, exact owned statement/branch coverage, repository/public-docstring contracts, security/SAST/dependency review, reproducible package, SBOM and provenance gates. Queued or predecessor evidence is non-passing.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Modifying tables*. https://www.postgresql.org/docs/18/ddl-alter.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Default values*. https://www.postgresql.org/docs/18/ddl-default.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: INSERT*. https://www.postgresql.org/docs/18/sql-insert.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html
