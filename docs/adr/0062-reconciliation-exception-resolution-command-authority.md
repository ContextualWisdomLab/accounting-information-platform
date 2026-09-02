# ADR 0062: Reconciliation exception resolution uses named immutable maker-checker commands

- Status: Proposed
- Date: 2026-09-02
- Bounded context: Bank Reconciliation / Evidence and Audit
- Depends on: ADR 0058 reconciliation-run command evidence and the stacked reconciliation lifecycle transition
- Follow-ups: #34/#9 for authenticated HTTP command authorization; #44 for least-privilege database capability

## Context

A reconciliation exception is an accounting control fact. The existing `reconciliation_exception.resolution_status_code` records `open`, `resolved`, or `superseded`, but a mutable status value by itself does not prove who reviewed the exception, why the reviewer accepted the outcome, which retained evidence supported it, when the decision became effective, or whether an idempotent command and its integration event committed together.

The reconciliation-run lifecycle therefore correctly treated every exception as a blocker. The next bounded buyer-visible gap is to make terminal exception state authoritative without weakening that fail-closed boundary. This decision does not create or post journals, alter ledger balances, close periods, or change accounting policy.

IFRS Accounting Standards do not prescribe an application-level bank-reconciliation command protocol. IAS 7 remains relevant to cash and cash-equivalent reporting, and the IASB is actively researching Statement of Cash Flows improvements in 2026; this ADR does not characterize the implementation as IFRS compliance. The control design instead treats maker-checker separation, retained evidence, transaction atomicity, tenant isolation, replay safety, and explicit valid/system-time causality as system control requirements.

## Decision

Introduce `accounting_core.reconciliation_exception_resolution_command` as immutable command evidence. A command is tenant/run/exception scoped and contains a shared reconciliation idempotency key, terminal target (`resolved` or `superseded`), retained evidence reference plus exact SHA-256 digest, a separate SHA-256 identity of the complete incoming JSON command, distinct reviewer actor, purpose, effective time, database recording time, and a database-derived command hash. The reviewed-artifact digest and command source-payload digest are deliberately separate facts: changing any received command member under the same key is an idempotency conflict even when the reviewed evidence artifact is unchanged.

The command uses the existing tenant-wide `reconciliation_command_identity` namespace with the new `exception_resolution` family. Opening, run-finalization, and exception-resolution commands therefore cannot silently reuse the same idempotency key for different meanings.

Exception resolution follows the reconciliation-run lifecycle serialization lock. The database verifies that the run is still `evaluating` or `review_required`, the exception is still `open`, the reviewer is not the exception owner, and the decision does not predate the exception. The exception identity, owner, next action, effective time, and recorded time are immutable from creation; changing assignment or other control evidence requires a future named command rather than a raw row rewrite. One exception may have only one terminal resolution command. Exact retries replay the retained command receipt; changed retries require a new idempotency key.

Because the command makes the exception terminal immediately, its valid/effective time cannot be later than its system recording time. Migration 0020 overwrites resolution-command `recorded_at` inside the database `BEFORE INSERT` authority trigger with `clock_timestamp()` and rejects `effective_at > recorded_at`. Migration 0024 applies the same system-time ownership to the source control facts that the command trusts: `reconciliation_exception.recorded_at` and `reconciliation_evidence.recorded_at` are overwritten by PostgreSQL at insertion, while their separate `effective_at` remains the business-valid-time fact. The migration does not reinterpret or rewrite valid time. This prevents privileged direct SQL from backdating retained review evidence or future-dating maker evidence to manufacture temporal provenance before maker-checker admission. A business process that needs a decision to become effective later must submit the resolution when it becomes effective or introduce a separately governed scheduling command; this command does not schedule future authority. PostgreSQL documents `clock_timestamp()` as the actual current time at invocation, and a row-level `BEFORE` trigger may modify `NEW` before insertion, which is the required system-time boundary here.

Migration 0024 also fails closed before installing that stronger provenance boundary when either source table already contains rows created under the pre-0024 schema. Before 0024, `recorded_at DEFAULT clock_timestamp()` could be overridden by an explicit INSERT value, and the surviving row contains no evidence that distinguishes a default-generated value from caller-supplied system time. The migration therefore creates transaction-scoped all-tenant `FOR SELECT TO current_user USING (true)` policies on the two FORCE-RLS tables, aborts with `reconciliation_recording_time_legacy_preflight` if any historical exception/evidence row exists, drops both temporary policies, and only then installs the durable recording-time triggers. A failed transaction rolls the temporary policies back. Creating a new reconciliation run does not cure retained pre-0024 rows; an operator must use a separately reviewed, auditable remediation/migration decision rather than silently deleting, rewriting, or grandfathering evidence whose system-time provenance cannot be proven.

A raw `UPDATE reconciliation_exception SET resolution_status_code = ...` is not authority. The database permits the `open` to terminal transition only when exactly one matching resolution command already exists in the same transaction. A deferred constraint requires the command and terminal status to commit as a pair. Tenant row-level security is forced and `PUBLIC` has no table privilege.

The application command writes the resolution command, terminal exception state, and transactional outbox event in one PostgreSQL transaction. `reconciliation_exception_resolved` and `reconciliation_exception_superseded` carry the exception aggregate reference and immutable command hash. The eventual restricted database capability in #44 must expose the named command boundary rather than grant raw insert/update/outbox privileges.

`REPEATABLE READ` is retained for the authority snapshot. A transaction waiting on the run lifecycle advisory lock may have established an older snapshot at that lock statement, so SQLSTATE `40001` is treated as a transaction-level retry signal. The command starts a fresh transaction and reevaluates the same source-payload identity; an overlapping exact retry therefore replays the winner's immutable receipt instead of manufacturing a second command or surfacing a stale-snapshot result. Non-`40001` failures are not retried by this path.

Run finalization is changed from the interim “any exception blocks” rule to: every exception must be terminal and must have exactly one durable resolution command whose target agrees with the terminal status. The application additionally includes the stable ordered resolution-command state—exception id, target, retained evidence reference/hash, and command hash—in the reconciliation transition snapshot. Changing resolution evidence therefore changes the run-finalization digest.

Migration 0020 refuses to install when migration-0019 data already contains a terminal reconciliation exception. Such a row predates named-command authority and cannot be silently grandfathered or backfilled with invented reviewer evidence. Migration 0013 already forces row-level security on `reconciliation_exception`, so the all-tenant upgrade preflight creates a transaction-scoped `FOR SELECT TO current_user USING (true)` migration policy before the scan and drops it immediately afterward, before changing durable command authority. This prevents a non-`BYPASSRLS` migration owner from observing an empty tenant-filtered history and falsely passing the preflight while leaving the durable tenant-isolation policy unchanged. The failed migration transaction leaves the previous schema intact; an operator must audit and remediate the legacy row under an explicit migration plan before retrying the upgrade.

No statement line, imported bank evidence, probabilistic suggestion, or LLM output can invoke this command automatically. It is a reviewed accounting-control decision. A resolved exception also does not post a journal; any correcting entry remains a separate authorized accounting command with its own evidence and idempotency boundary.

## Consequences

The run lifecycle can now progress after exceptions are explicitly reviewed without treating mutable status as evidence. Auditors can trace a terminal exception to a named reviewer decision, retained evidence digest, full command source-payload digest, exact database-derived command hash, database-owned system recording time, and emitted event. The exception and retained review-evidence source rows also receive database-owned system time at creation, so the later immutable chain does not depend on caller-shaped `recorded_at`. Concurrent finalization and exception resolution serialize on the same aggregate lock, while PostgreSQL `REPEATABLE READ` plus bounded transaction retry preserves one coherent authority decision across serialization races.

A future-effective resolution is deliberately fail-closed instead of being stored as if already terminal. This keeps the current `resolution_status_code` semantically aligned with usable accounting-control authority. Supporting scheduled future resolution would require a separate pending/scheduled state and activation command rather than overloading the terminal-state mutation implemented here.

The stronger recording-time boundary is intentionally migration-disruptive for a populated unreleased pre-0024 database: historical control rows cannot be promoted to database-owned system-time evidence merely by installing a new trigger. Release/upgrade automation must stop, preserve a restorable backup and exact row inventory, and require an explicit reviewed remediation plan before retrying 0024. This is preferable to inventing provenance that cannot be demonstrated from retained database evidence.

The forward migrations are unreleased and stacked. They must pass real PostgreSQL installation, rollback/restore rehearsal, direct-SQL bypass tests, concurrency tests, exact replay/conflict tests, forced-RLS tests, 100% owned production statement/branch coverage, security scans, package/SBOM/provenance gates, and independent review before integration. Databases containing pre-migration terminal exception rows or any pre-0024 exception/evidence rows require explicit review because those rows predate the applicable authority boundary; they must not be silently grandfathered as valid maker-checker or system-time evidence.

## Alternatives rejected

**Keep `resolution_status_code` as the only fact.** Rejected because a privileged or accidental update could manufacture terminal control state without durable reviewer provenance.

**Store free-form resolution notes on the exception row.** Rejected because notes do not provide immutable command identity, exact replay, maker-checker enforcement, or an atomic integration event.

**Treat the reviewed-evidence digest as the command identity.** Rejected because the reviewed artifact and the incoming command are different provenance objects. An idempotency key must not replay a materially changed command merely because it still points at the same reviewed artifact.

**Trust `DEFAULT clock_timestamp()` as a system-time guarantee.** Rejected because a default is applied only when the caller omits the column. A privileged direct SQL insert can otherwise supply arbitrary `recorded_at`, which is incompatible with using that value as system-time provenance.

**Grandfather pre-0024 `recorded_at` values.** Rejected because the stored row does not record whether its timestamp came from the database default or from an explicit caller value. Treating either as proven database system time would manufacture evidence after the fact.

**Grandfather pre-0020 terminal rows.** Rejected because the database cannot reconstruct the missing reviewer, purpose, command source payload, or atomic outbox evidence without inventing historical authority.

**Let reconciliation finalization infer that every non-`open` status is approved.** Rejected because it promotes historical mutable state into authority and makes the finalization snapshot blind to the evidence that justified the exception decision.

**Allow future-effective commands to set terminal status immediately.** Rejected because the current schema has no scheduled/pending activation state. Immediate terminalization would make close evidence effective before the maker-checker decision's own valid time and collapse valid time into caller-controlled system time.

**Post a correcting journal as part of exception resolution.** Rejected because bank reconciliation review and General Ledger posting are distinct bounded-context commands. Resolution may identify that a correcting journal is needed, but posting must remain a separate authorized double-entry operation.

## Verification and traceability

Acceptance evidence must demonstrate at least these cases on real PostgreSQL: raw terminal status update fails; open exception maker evidence cannot be rewritten before review; caller-supplied exception and retained review-evidence `recorded_at` values are replaced by database time; owner-as-reviewer fails; decision before exception time fails; a future-effective decision fails with no command, status, or outbox side effect even if a caller attempts to supply recording time; valid command changes status and emits the matching outbox event atomically; exact replay returns the same receipt; any changed incoming command payload conflicts; overlapping exact retries converge through a fresh transaction after serialization failure; terminal exception evidence cannot be rewritten; migration installation refuses legacy terminal rows without fabricating command evidence even when the migration role does not bypass forced RLS; migration 0024 rejects any populated pre-0024 exception/evidence store under a non-`BYPASSRLS` migration owner and succeeds on the empty control population while removing its temporary visibility policies; a terminal status without matching command still blocks run reconciliation; a terminal status with matching command can proceed when all other bridge/review controls pass; tenant RLS and shared command idempotency remain enforced.

Primary-source alignment used for this decision:

- International Accounting Standards Board. (2026). *Statement of Cash Flows and Related Matters*. IFRS Foundation. https://www.ifrs.org/projects/work-plan/statement-of-cash-flows-and-related-matters/
- International Organization for Standardization. (2022). *ISO/IEC 27001:2022 Information security, cybersecurity and privacy protection—Information security management systems—Requirements*. ISO. https://www.iso.org/standard/27001
- National Institute of Standards and Technology. (2020, updated 2025). *Security and Privacy Controls for Information Systems and Organizations (NIST SP 800-53 Rev. 5), AC-5 Separation of Duties*. U.S. Department of Commerce. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Default Values*. https://www.postgresql.org/docs/18/ddl-default.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: INSERT*. https://www.postgresql.org/docs/18/sql-insert.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Date/Time Functions and Operators*. https://www.postgresql.org/docs/18/functions-datetime.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of Trigger Behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction Isolation*. https://www.postgresql.org/docs/18/transaction-iso.html
- PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Row Security Policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

These references support reporting context, segregation-of-duty intent, default-value limits, database-owned wall-clock recording, trigger authority, transactional snapshot semantics, transaction-retry behavior, forced-RLS upgrade visibility, and tenant-access enforcement. They are design inputs, not certification or compliance claims.
