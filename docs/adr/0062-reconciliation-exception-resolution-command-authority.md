# ADR 0062: Reconciliation exception resolution uses named immutable maker-checker commands

- Status: Proposed
- Date: 2026-09-02
- Bounded context: Bank Reconciliation / Evidence and Audit
- Depends on: ADR 0058 reconciliation-run command evidence and the stacked reconciliation lifecycle transition
- Follow-ups: #34/#9 for authenticated HTTP command authorization; #44 for least-privilege database capability

## Context

A reconciliation exception is an accounting control fact. `reconciliation_exception.resolution_status_code` by itself does not prove who reviewed the exception, why the reviewer accepted the outcome, which retained evidence supported it, when the decision became effective, or whether the idempotent command and integration event committed together. Reconciliation completion therefore needs a named maker-checker command rather than mutable status as authority.

The command also depends on two temporal facts that must remain distinct. `effective_at` is business-valid time. `recorded_at` is AIS system knowledge time. Before migration 0024, the exception and retained-evidence tables declared `recorded_at DEFAULT clock_timestamp()`, but PostgreSQL defaults can be overridden by an explicit INSERT value. A privileged writer could therefore manufacture the chronology later used by maker-checker admission.

A first migration-0024 design rejected every populated pre-0024 exception/evidence store. That protected against silently promoting unverifiable timestamps, but it was not a viable enterprise upgrade boundary: migration 0020 had already made retained reconciliation evidence immutable and the accounting contract forbids deleting or rewriting audit evidence merely to satisfy a migration. The product requires a non-destructive forward path that preserves old facts while refusing to treat their unprovable timestamp source as trusted chronology.

IFRS Accounting Standards do not prescribe this application/database protocol. IAS 7 remains relevant to cash and cash-equivalent reporting, but this ADR makes no certification or compliance claim. Maker-checker separation, retained evidence, transaction atomicity, tenant isolation, replay safety, and explicit valid/system-time provenance are system-control requirements.

## Decision

Introduce `accounting_core.reconciliation_exception_resolution_command` as immutable command evidence. A command is tenant/run/exception scoped and contains a shared reconciliation idempotency key, terminal target (`resolved` or `superseded`), retained evidence reference plus exact SHA-256 digest, a separate SHA-256 identity of the complete incoming JSON command, distinct reviewer actor, purpose, effective time, database recording time, and a database-derived command hash. Reviewed-artifact identity and incoming-command identity remain separate; changing any received command member under one key is an idempotency conflict even when the reviewed artifact is unchanged.

The command uses the tenant-wide `reconciliation_command_identity` namespace with the `exception_resolution` family. Opening, run-finalization, and exception-resolution commands therefore cannot silently reuse one idempotency key for different meanings.

Exception resolution follows the reconciliation-run lifecycle serialization key. PostgreSQL verifies that the run is `evaluating` or `review_required`, the exception is open, the reviewer differs from the exception owner, and the decision does not predate the exception. Exception identity, owner, next action, effective time, and recording-time provenance are immutable from creation. One exception may have only one terminal resolution command. Exact retries replay the retained command receipt; changed retries fail closed.

### Recording-time authority and upgrade

Migration `0020_reconciliation_exception_resolution_command.sql` already makes the resolution command's own `recorded_at` database-owned with `clock_timestamp()` and rejects `effective_at > recorded_at`. Migration `0024_reconciliation_control_recording_time_authority.sql` extends that system-time boundary to the exception and retained review artifact without rewriting historical chronology.

Migration 0024 adds `recording_time_authority_code` to both `accounting_core.reconciliation_exception` and `accounting_core.reconciliation_evidence`. Existing rows receive the constant marker `legacy_unverified`; their original `recorded_at` values remain unchanged. PostgreSQL 18 documents that adding a column with a constant default can be represented in table metadata rather than rewriting every existing row. The migration then drops the column defaults so callers cannot use a default as the authority mechanism.

For every post-0024 INSERT, a `BEFORE INSERT` trigger overwrites both `recorded_at` and `recording_time_authority_code`, assigning `clock_timestamp()` and `database_clock`. Dedicated UPDATE guards make that timestamp/authority pair immutable. The broader retained-evidence immutability rule remains in force.

A second `BEFORE INSERT` guard on `reconciliation_exception_resolution_command` requires the exact exception and retained review evidence selected by the existing command-hash trigger to have `recording_time_authority_code = 'database_clock'`. PostgreSQL fires triggers of the same kind in alphabetical trigger-name order; the hash guard therefore resolves `NEW.reconciliation_evidence_id` before the recording-time authority guard checks the selected evidence. Legacy rows remain fully queryable audit evidence but cannot be newly terminalized through maker-checker authority that depends on trusted system chronology.

The supported public command does not reinterpret or bypass that database rejection. When PostgreSQL rejects a resolution with SQLSTATE `23514` and the exact `reconciliation_resolution_recording_time_authority_required` invariant marker, `resolve_reconciliation_exception()` rolls the transaction back and translates only that named persistence failure into an actionable `AccountingValidationError`. The original database error remains chained as the cause for operator diagnostics. Other check violations and unrelated database failures are not relabelled as recording-time failures. This keeps PostgreSQL as the independent chronology authority while preventing a provider-specific driver exception from escaping the accounting command boundary.

This is intentionally not a historical-provenance repair. The database cannot prove whether a pre-0024 timestamp came from the old default or from a caller, so it must not relabel that row as `database_clock`. Operators preserve the historical run and create a new reconciliation run/review evidence after migration when a new authoritative decision is required. No old audit row is deleted, rewritten, or grandfathered.

### Command/status/outbox and lifecycle authority

A raw `UPDATE reconciliation_exception SET resolution_status_code = ...` is not authority. PostgreSQL permits the open-to-terminal transition only when exactly one matching immutable resolution command exists in the same transaction. Deferred constraints require command, terminal status, and the matching accounting outbox event to commit together. Later migrations retain exactly one matching authority event after commit and reserve reconciliation authority event types for exact immutable command-backed identities; `published_at` remains bounded publication metadata rather than accounting authority.

`REPEATABLE READ` is retained for the authority snapshot. A transaction that loses a serialization race restarts on SQLSTATE `40001`, reloads committed command/evidence, and re-evaluates the same source-payload identity. Non-`40001` failures are not retried by this path.

Run finalization changes from the interim “any exception blocks” rule to: every exception must be terminal and backed by exactly one durable resolution command whose target agrees with status. The lifecycle snapshot includes the stable ordered resolution-command population without replacing the parent-owned statement/book population identities or exact book-to-bank bridge.

Migration 0020 still refuses installation over pre-0020 terminal exception rows lacking named-command provenance. That is a different defect from recording-time authority: a terminal row cannot be grandfathered because the database cannot reconstruct the missing reviewer, purpose, source command, or atomic outbox evidence without inventing history. The checked-in bounded remediation applies only when retained evidence can prove the missing command lineage; otherwise the legacy database is preserved for audit and a new authoritative run is created after the migration chain is installed.

No bank statement, probabilistic suggestion, model output, or direct foreign system writes this command automatically. Reconciliation exception resolution cannot post or reverse a journal, close a period, or alter accounting policy. Any correcting journal is a separate General Ledger command.

## Consequences

Auditors can distinguish legacy chronology from database-owned chronology row by row. A populated database can move forward through migration 0024 without deleting immutable control history, while any new maker-checker decision that relies on system time fails closed unless both maker and retained review evidence were recorded under the database-owned boundary.

The new provenance column is normalized row-level evidence rather than a global upgrade flag. It travels with the exact exception/review facts whose chronology matters, keeps the existing aggregate boundaries intact, and uses semantic multiword snake_case naming. The marker is not a substitute for the timestamp; it records how the timestamp's authority was established.

A future-effective resolution remains fail-closed. Supporting scheduled activation would require an explicit pending/scheduled state and activation command rather than overloading terminal status.

The forward migrations remain Proposed and unreleased. They must pass real PostgreSQL upgrade/install behavior, direct-SQL bypass tests, concurrency, exact replay/conflict, forced-RLS, 100% owned production statement/branch coverage, repository/public-docstring contracts, security scans, reproducible package/SBOM/provenance, and independent exact-head review before integration.

## Alternatives rejected

**Keep `resolution_status_code` as the only fact.** Rejected because a privileged or accidental update could manufacture terminal control state without durable reviewer provenance.

**Use the reviewed-evidence digest as the complete command identity.** Rejected because the reviewed artifact and received command are separate provenance objects.

**Trust `DEFAULT clock_timestamp()` as system-time authority.** Rejected because an explicit INSERT value overrides the default.

**Abort migration 0024 whenever historical rows exist.** Rejected after test-first review because it creates a permanent upgrade dead end for populated stores while the same product contract forbids destructive history edits.

**Backfill historical rows with migration time or mark them database-owned.** Rejected because either choice manufactures provenance that the old row cannot prove.

**Store one database-level legacy flag.** Rejected because authority is attached to each retained fact and command admission must inspect the exact rows it uses.

**Preserve historical timestamps with `legacy_unverified`, use `database_clock` only for post-0024 rows, and gate commands on that row-level provenance.** Selected because it preserves audit evidence, permits forward migration, and fails closed exactly where trusted chronology becomes authority.

**Post a correcting journal as part of exception resolution.** Rejected because reconciliation review and General Ledger posting are separate bounded-context commands.

## Verification and traceability

Acceptance evidence must demonstrate on real PostgreSQL: raw terminal status update fails; maker evidence cannot be rewritten; caller-supplied post-0024 `recorded_at` and authority markers are overwritten by the database; pre-0024 forged timestamps survive migration unchanged as `legacy_unverified`; new rows become `database_clock`; recording-time provenance cannot be mutated; a resolution command cannot use legacy exception/review chronology; the supported public command translates only the named recording-time authority rejection into an actionable accounting-domain error while retaining the database error as cause and leaving unrelated check violations unchanged; owner-as-reviewer fails; invalid temporal ordering fails; valid command/status/outbox commit atomically; exact replay returns the original receipt; changed payload conflicts; overlapping exact retries converge through full transaction restart; migration 0020 still refuses unsupported legacy terminal authority; tenant RLS/shared idempotency remain enforced; and one unchanged exact head passes all repository/security/package/review gates.

Primary-source alignment:

- International Accounting Standards Board. (2026). *Statement of Cash Flows and Related Matters*. IFRS Foundation. https://www.ifrs.org/projects/work-plan/statement-of-cash-flows-and-related-matters/
- International Organization for Standardization. (2022). *ISO/IEC 27001:2022 Information security, cybersecurity and privacy protection—Information security management systems—Requirements*. ISO. https://www.iso.org/standard/27001
- National Institute of Standards and Technology. (2020, updated 2025). *Security and Privacy Controls for Information Systems and Organizations (NIST SP 800-53 Rev. 5), AC-5 Separation of Duties*. U.S. Department of Commerce. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Modifying tables*. https://www.postgresql.org/docs/18/ddl-alter.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Default values*. https://www.postgresql.org/docs/18/ddl-default.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Date/time functions and operators*. https://www.postgresql.org/docs/18/functions-datetime.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

These are design inputs, not certification claims.
