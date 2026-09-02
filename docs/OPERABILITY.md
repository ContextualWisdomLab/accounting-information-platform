# Operability and Recovery Baseline

## Deployment preconditions

Use PostgreSQL 18 and keep the migration owner, application runtime login and administrative / break-glass identities separate. Apply migrations in the checked-in authority order through `0022_reconciliation_authority_outbox_retention.sql` before starting the service. Do not run the application with a table-owner, superuser or `BYPASSRLS` login.

Required environment values are deployment-specific. At minimum, configure the accounting database URL and bind this AIS process to exactly one tenant reference. Secrets belong in an approved secret store; do not place database passwords, NTS credentials, bearer tokens or provider secrets in journal payloads, logs or outbox events.

`X-CWL-Tenant-Reference` is a tenant-binding header, **not** caller authentication. The standalone runner binds to `127.0.0.1` when no host is explicitly supplied. Do not expose the HTTP listener directly to untrusted networks. A non-loopback bind must be an explicit deployment decision behind a trusted authentication / authorization boundary, and the validated caller tenant must match the AIS tenant binding.

## Database installation

Apply, in order:

```text
database/migrations/0001_accounting_foundation.sql
database/migrations/0002_chart_account_class.sql
database/migrations/0003_home_tax_submission.sql
database/migrations/0004_close_idempotency_key.sql
database/migrations/0005_closed_period_guard.sql
database/migrations/0006_concurrency_hot_partition.sql
database/migrations/0007_runtime_tenant_binding.sql
database/migrations/0008_fiscal_period_open_command.sql
database/migrations/0009_accounting_book_period_control.sql
database/migrations/0010_soft_close_command_evidence.sql
database/migrations/0011_bank_statement_evidence.sql
database/migrations/0012_bank_assignment_command_identity.sql
database/migrations/0013_reconciliation_run_exception_evidence.sql
database/migrations/0014_reconciliation_candidate_allocation.sql
database/migrations/0015_reconciliation_multi_match_conservation.sql
database/migrations/0016_reconciliation_approval_evidence.sql
database/migrations/0017_reconciliation_approval_lock_order.sql
database/migrations/0018_bank_statement_balance_evidence.sql
database/migrations/0019_reconciliation_run_command_evidence.sql
database/migrations/0019_reconciliation_run_database_snapshot_authority.sql
database/migrations/0020_reconciliation_exception_resolution_command.sql
database/migrations/0021_reconciliation_exception_resolution_outbox_pair.sql
database/migrations/0022_reconciliation_authority_outbox_retention.sql
```

Migration `0015_reconciliation_multi_match_conservation.sql` replaces the run-wide single-approved-match shortcut from `0014` with tenant/run-scoped match identity plus exact statement/journal allocation conservation. It permits multiple independently approved matches, including split and aggregate allocation populations, only when no authoritative source amount is over-consumed and grants no journal-posting authority.

Migration `0016_reconciliation_approval_evidence.sql` adds immutable tenant/run/match-scoped human approval evidence. Operators first record the reviewed approval command identity, immutable object-storage source-payload hash/reference, approver and purpose, then transition the proposed match to `approved`; PostgreSQL computes and stores a SHA-256 snapshot of the candidate/allocation rows, rejects status-only or stale-snapshot approval, freezes candidate identity and later allocations, and refuses installation over existing non-proposed matches without durable approval evidence. Approval evidence grants no journal-posting, reversal, close, or policy authority.

Migration `0017_reconciliation_approval_lock_order.sql` is a forward repair for databases that already applied `0016`. It makes approval-evidence insertion lock the proposed `reconciliation_match` row before taking the per-match snapshot advisory lock, matching allocation insertion and terminal match transitions. This prevents a row/advisory deadlock under concurrent approval and allocation attempts.

Migration `0018_bank_statement_balance_evidence.sql` preserves the exact numeric amount, currency, credit/debit direction, typed effective date/time, sequence, locator, source hash, and standard-versus-proprietary balance-type discriminator for every camt.053 balance. The effective date/time is distinct from statement period and system `recorded_at`; existing balance hashes remain on the statement row for compatibility. The numeric rows are immutable, forced-RLS evidence that a reconciliation bridge may read but that cannot post, reverse, or mutate a journal.

Migration `0019_reconciliation_run_command_evidence.sql` records the immutable command identity that opens a reconciliation run from one persisted bank statement and active bank-account assignment, then adds the evidence-derived run-finalization command and shared reconciliation command-identity namespace. The tenant-scoped idempotency key, command hash, source hash, and object-store reference are forced-RLS evidence; new runs exclude source facts recorded after `knowledge_cutoff_at`, and a deferred database guard requires one command per run with statement-to-assignment bank-account provenance. The public run API opens only `evaluating` scope and cannot post journals or close periods.

The unreleased lifecycle-parent overlay `0019_reconciliation_run_database_snapshot_authority.sql` must run after the base 0019 migration and before migrations 0020/0021/0022. It defines `accounting_core.reconciliation_run_database_snapshot_authority`, independently reconstructs the exact bank-statement and assigned cash-book populations and bridge, and replaces caller-selected transition snapshot, statement-population, and book-population identities. The supported installer treats this overlay as part of the complete chain even though its filename shares the base numeric prefix; do not sort migrations by filename alone and accidentally omit it.

Migration `0020_reconciliation_exception_resolution_command.sql` makes exception resolution a named maker-checker command rather than a mutable status shortcut. The database requires an active reviewable run, one open tenant/run/exception, a reviewer distinct from the exception owner, retained evidence reference/hash, temporal causality, the shared reconciliation idempotency namespace, and a database-derived command hash. A raw terminal status update is rejected unless its matching command already exists in the transaction; a deferred pair guard requires command and terminal status to commit together, then terminal exception evidence is immutable. Run finalization accepts an exception only when its terminal status and retained resolution command agree. This command does not post a journal; any correcting journal is a separate authorized General Ledger command.

Migration `0021_reconciliation_exception_resolution_outbox_pair.sql` extends that deferred database boundary so every immutable exception-resolution command must also commit exactly one matching accounting outbox event. PostgreSQL derives the required event type from the terminal decision and binds tenant, exception aggregate reference, resolution-command payload reference, and the database-assigned command hash. The same migration adds the child resolution-evidence snapshot overlay: parent `accounting_reconciliation_transition_database_authority_guard` derives the exact bridge and all three server-owned transition identities first; `accounting_reconciliation_transition_evidence_snapshot_guard` composes the immutable ordered exception-resolution command population second while preserving the parent statement/book references; `accounting_reconciliation_transition_hash_guard` binds the final snapshot last. Direct SQL that commits command/status authority without the matching publication receipt, supplies forged transition identities, omits durable resolution-command evidence, or presents an untied bridge fails closed.

Migration `0022_reconciliation_authority_outbox_retention.sql` preserves the exactly one matching outbox event invariant after command commit for both reconciliation exception-resolution and run-lifecycle authority. Deferred outbox-side guards validate the old identity on DELETE or identity re-key and the new identity on INSERT or identity re-key, so a bound event cannot disappear and a duplicate event cannot be inserted or manufactured by updating an unrelated row. Publication remains supported because `published_at` is not part of the guarded authority identity. The migration preflight refuses any existing reconciliation command with zero or multiple matching events; restore or reconstruct verified provenance before retrying and never fabricate evidence to satisfy the migration.

Migration `0007_runtime_tenant_binding.sql` replaces caller-selected tenant authority with owner-controlled runtime-login binding. Migration `0008_fiscal_period_open_command.sql` adds forced-RLS, append-only command evidence so fiscal-period-open retries are bound to the original tenant key and source hash. Both must be installed before runtime database privileges are treated as production-ready.

After installation, prove with the actual runtime login that supported reads and writes work for its tenant, another tenant is inaccessible, the login is not a migration owner / superuser / `BYPASSRLS`, and direct SQL cannot bypass journal immutability, period controls, reconciliation lifecycle controls, or exception-resolution authority.

## Concurrency and hot-write operations

The multithreaded HTTP server gives each request an independent PostgreSQL
transaction. Each new session bounds lock waits to five seconds and idle
transactions to sixty seconds. State-changing proposal, adjusting, reversal,
HomeTax, period-open, period-close, reconciliation-run, and ordinary command
paths acquire tenant-scoped transaction advisory locks through
`pg_advisory_xact_lock`; those locks live until the surrounding transaction ends.
Reconciliation run finalization is intentionally different: it acquires the
run-lifecycle advisory key with session-scoped `pg_advisory_lock` on the owning
database connection before opening the `REPEATABLE READ` authority snapshot,
holds that session lock across the evidence read and transaction, and explicitly
releases it with `pg_advisory_unlock` in the `finally` path. Exception resolution
uses the same tenant/run lifecycle key through the transaction-scoped command-lock
helper after transaction isolation begins; SQLSTATE `40001` retries restart the
whole resolution transaction so a waiter reloads the committed command/evidence.
Posting/reversal re-read their selected period after acquiring the shared period
lock; close selects the period row with `FOR UPDATE` before evaluating its package.
A lock timeout rolls back the transaction and must be retried after the operator
resolves the competing command.

Migration `0006_concurrency_hot_partition.sql` adds tenant-leading indexes to
the high-write proposal, journal, line, reversal, receipt, HomeTax, and outbox
populations. Monitor `pg_stat_activity`, `pg_locks`, lock-wait duration, and
index usage before introducing physical hash-by-tenant/time partitions. A
partition migration must preserve every tenant-scoped primary/unique key,
foreign key, RLS policy, idempotency decision, and outbox ordering invariant.

## Reconciliation exception operations

Use `resolve_reconciliation_exception()` only after an authorized reviewer has inspected the retained exception evidence. The exception owner and resolution actor must differ. Supply the exact evidence reference and canonical SHA-256 digest, purpose code, effective time, and a new reconciliation idempotency key. Exact replay under the original key returns the immutable receipt; changed evidence under that key is a conflict and requires a new reviewed command.

Do not repair an exception by updating `resolution_status_code` directly, and do not grant a runtime role raw insert/update/outbox DML as a substitute for the named command. The future least-privilege database capability tracked in issue #44 must expose the command boundary. HTTP exposure remains blocked until the purpose-bound authentication/authorization path tracked in #34 / issue #9 binds the authenticated principal to the command; payload actor strings are evidence, not authentication.

Migration `0020` deliberately refuses installation while pre-0020 terminal exception rows exist without named maker-checker command provenance. Creating a new reconciliation run does **not** satisfy that preflight because the legacy terminal rows remain in the database. Stop the upgrade/release path, take and verify a restorable backup, inventory every affected tenant/run/exception, and use the checked-in `scripts/repair_pre_0020_exception_resolution.py` only as the bounded, explicitly reviewed remediation path for provenance that can be reconstructed from retained review evidence. Record the operator, source evidence, before/after row identities and validation result as migration evidence, rerun the 0020 preflight, and only then continue through 0022. If historical maker-checker provenance cannot be proven, do not synthesize it; preserve the legacy database for audit and use a new reconciliation run only after the authority migrations have been installed in the target operational path.

## Purpose-limited soft-close authorization

`accounting_closing_writer` is a `NOLOGIN` capability role. Migration `0005` reasserts `NOLOGIN` even if a role with that name already existed.

Ordinary application logins must **not** receive this membership. A purpose-limited connection identity used for an approved soft-close adjustment, close entry or reversal may receive membership through the deployment owner-control path:

```sql
GRANT accounting_closing_writer TO <purpose_limited_closing_login>;
```

The login itself must be the session identity checked by PostgreSQL. Merely setting `accounting_core.journal_write_role` does not grant authority. The application transaction sets the classification GUC only after the caller has already been routed through the approved command path.

When the capability is no longer required, revoke it through the same controlled administrative path:

```sql
REVOKE accounting_closing_writer FROM <purpose_limited_closing_login>;
```

Record the grant / revoke as operational evidence. Do not grant `accounting_closing_writer` to the generic proposal-ingest runtime role.

## Posting and replay

Every monetary command uses exact decimal strings. Do not send JSON floating-point numbers for journal amounts. A successful proposal posts proposal evidence, journal header / lines, posting receipt and transactional outbox evidence atomically.

For a normal proposal retry, reuse the original tenant-scoped idempotency key only when the immutable payload evidence is identical. Changed evidence under the same key is a conflict and requires correction at the source, not a new journal under the old key.

For a fiscal-period-open retry, reuse the original `idempotency_key` and `source_payload_hash`. Exact replay returns the recorded open result even if that period has subsequently closed; changed scope, dates, or source hash under the same key is an idempotency conflict. A different command key may acknowledge an already-open matching period, but it cannot reopen a soft- or hard-closed period.

Posted journal facts are append-only. Never repair a posted journal with SQL `UPDATE`, `DELETE`, `TRUNCATE` or destructive migration logic. Use explicit reversal and, when appropriate, a separately posted replacement.

## Reversal operations

A reversal identifies a posted original journal and posts equal-and-opposite lines. The original remains posted and queryable. A reversal date may not precede the original accounting date.

Soft-closed periods can admit an approved reversal only through the purpose-limited closing-writer database capability. Hard-closed periods reject a new reversal into the locked period.

Current PostgreSQL integration tests prove exact reversal replay is bound to tenant, reversal command idempotency identity, original journal reference, reversal date/reason and immutable reversal-command evidence hash. The same command replays the original receipt; changed immutable command evidence fails closed instead of returning an earlier receipt. Treat this behavior as current foundation capability, while release readiness still requires the unchanged exact head to pass every applicable CI/security/package/review/governance gate together.

## Close operations

### Soft-close

Soft-close changes the period to `soft_closed`, writes no hard-close trial-balance snapshot and blocks ordinary posting. Authorized closing, adjusting and reversal paths require both the transaction classification and `accounting_closing_writer` membership.

### Hard-close

Hard-close loads the close binder in one repeatable-read view, posts the AIS period-closing journal when required, stores the hard-close snapshot and changes the period to `hard_closed`. Hard-close is irreversible in this foundation. Open a later period for subsequent activity.

If close fails, inspect the first causal missing catalog / mapping / balance / period error. Do not invent a snapshot or mark a period closed manually.

## Billing proposal pull

Configure one primary `BILLING_BASE_URL` and, only when required, additional `BILLING_ALLOWED_ORIGINS`. The request body cannot authorize a new destination.

Configured origins must be valid HTTP(S) origins. Malformed ports and malformed IPv6 are validation errors. `localhost`, loopback and link-local addresses are rejected even when explicitly listed in the allowlist; the allowlist is not an SSRF bypass switch.

Billing list responses use only `journal_proposals` and `next_cursor`. Reject invented list envelope keys or malformed cursor values instead of guessing.

The pull is page-progressive, not an all-pages distributed database transaction:

- If the **first** Billing GET fails, no proposal from that pull has been posted.
- If a **later** page fails, postings already committed from earlier pages remain authoritative.
- Retry the same pull boundary. Exact proposal idempotency returns the existing posting receipts and prevents duplicate journals.
- A repeated / non-advancing cursor or a pull beyond the configured page bound fails closed.

Do not describe a later-page remote failure as rolling back earlier committed accounting facts.

## HomeTax evidence

`POST /home-tax-submissions` is fail-closed evidence handling, not an NTS transport. The command requires the tenant-bound scope, a non-empty command idempotency key, a loadable VAT period register and the purpose-limited `ACCOUNTING_HOMETAX_CREDENTIAL` presence check.

The current foundation never returns `submission_status_code=transmitted`. Missing credential returns a rejected receipt with `hometax_credential_missing`; a present credential still returns `hometax_transport_unavailable` until a separately reviewed transport exists. Never log or echo the credential.

## HTTP request boundaries

Request bodies are bounded. Oversized JSON commands are rejected before domain work. Invalid accounting inputs return a client error with a next action; they must not escape as raw parser / URL / database exceptions.

Health checks do not prove accounting readiness. A process can be healthy while exact-head CI, PostgreSQL integration, security or independent review is still missing.

## Outbox and audit

Posting, reversal, close, reconciliation finalization, and reconciliation exception-resolution commands commit their outbox evidence in the same accounting transaction. For committed reconciliation finalization and exception-resolution commands, PostgreSQL must retain exactly one matching outbox event: deleting or re-keying the sole event, inserting a duplicate, or re-keying an unrelated event into the same authority identity fails closed. Publishing an outbox row may update only publication metadata such as `published_at`; it does not rewrite the accounting fact or authority identity. Audit reads include published and unpublished evidence but do not publish rows themselves.

Do not place raw card data, passwords, tokens, model prompts or unnecessary PII in outbox / audit payloads. PII necessary for authorized accounting work remains purpose-bound and access-logged.

## Failure handling

For every failed job or operational command:

1. identify the exact source head / checkout SHA;
2. inspect the failing job log or database error first;
3. locate the first causal boundary rather than the final symptom;
4. compare with a working path;
5. state one falsifiable hypothesis;
6. add or strengthen a realistic failing regression;
7. apply the smallest root-cause-changing repair;
8. rerun the failed boundary and then the full applicable gates.

Do not transfer a success from a predecessor SHA or synthetic merge ref to the current head.

## Backup, restore and recovery

Before release, rehearse clean install, forward migration, rollback strategy, backup restore and point-in-time recovery with production-like data volumes and the non-owner runtime identity. Restoration must preserve immutable journal / receipt / outbox lineage and tenant isolation.

Recovery from an accounting error is not database row editing. Restore infrastructure only for infrastructure loss; correct economic facts with reversal / reposting according to accounting policy.

## Package provenance and attestations

Every pull-request package build first verifies the exact PR head checkout, derives `SOURCE_DATE_EPOCH` from that commit, builds the wheel twice, and requires byte-identical SHA-256 digests. `scripts/generate_supply_chain_evidence.py` then emits a deterministic `source-provenance.json` that binds the verified source SHA, source timestamp, wheel file/digest and SPDX SBOM digest. `SHA256SUMS` covers the wheel, SBOM and source-provenance manifest, and the uploaded artifact keeps all four files together.

The PR-capable `accounting-foundation` job has `contents: read` only. It does not receive OIDC, attestation, or artifact-metadata write authority while executing repository-controlled tests and build code. Do not move those permissions back into that job merely because individual attestation steps are conditional; GitHub permissions apply to the whole job.

Do not accept a GitHub OIDC attestation created by a `pull_request` event as exact PR-head provenance merely because the build checked out the PR head. The attestation signing context can identify GitHub's synthetic pull-request merge ref and merge commit. Repository CI therefore reserves signed provenance and SBOM attestations for the separate `integrated-attestations` job. That job is job-level push-only for `develop` or `main`, depends on a successful `accounting-foundation` build, downloads the SHA-named evidence bundle, verifies `SHA256SUMS` and `source-provenance.json.source_sha == github.sha`, and only then receives `id-token: write`, `attestations: write`, and `artifact-metadata: write`. The protected-head push must reproduce the package and those signed attestations must succeed before release. This control is evidence readiness only; it does not claim a SLSA level or certification.

## Release acceptance

Release only from one unchanged protected head after all applicable evidence passes together: PostgreSQL integration, exact 100% owned production statement and branch coverage, public API docstrings, repository contracts, SAST / security, reproducible package build and install, deterministic exact-source provenance, SPDX SBOM, protected-head OIDC provenance/SBOM attestations, migration rehearsal, recovery evidence and qualifying independent review. Queued, cancelled, stale, predecessor or status-only evidence is non-passing. An applicable gate that is skipped is non-passing; the protected-head attestation job is intentionally not applicable to a pull-request event and becomes mandatory on the integrated `develop`/`main` push.

## Runtime database tenant provisioning

Before routing accounting traffic to a new database login, an owner-controlled operator records that login's current PostgreSQL role OID, role name, and tenant in `accounting_core.runtime_tenant_binding`. The runtime login itself must have no direct privilege on the binding table. Recreating a role, restoring into a new cluster, or intentionally reassigning a tenant requires a fresh binding because the role OID is part of the identity. An unbound runtime or a requested tenant that disagrees with the binding fails closed; do not restore service by setting `app.tenant_account_id`.

## Accounting-book close isolation

Apply `0009_accounting_book_period_control.sql` after `0008_fiscal_period_open_command.sql` before granting runtime access. After migration, verify that closing one book leaves an open sibling book postable and that direct SQL into the closed book fails at `guard_period_insert`. If a book-period control row is missing, repair catalog/control state before retrying close; do not edit posted journals.

## Soft-close command evidence recovery

New soft-close transitions atomically retain the original idempotency key, source-journal count and canonical source hash. Replays must use the same key and return those stored facts even after authorized adjustments change the live ledger. If a legacy migrated `soft_closed` row has no command evidence, the service fails closed; restore the original evidence through an audited migration only if it can be proven. Never synthesize historical evidence from current journals.