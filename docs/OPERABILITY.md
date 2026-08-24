# Operability and Recovery Baseline

## Deployment preconditions

Use PostgreSQL 18 and keep the migration owner, application runtime login and administrative / break-glass identities separate. Apply migrations in numeric order through `0011_bank_statement_evidence.sql` before starting the service. Do not run the application with a table-owner, superuser or `BYPASSRLS` login.

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
```

Migration `0007_runtime_tenant_binding.sql` replaces caller-selected tenant authority with owner-controlled runtime-login binding. Migration `0008_fiscal_period_open_command.sql` adds forced-RLS, append-only command evidence so fiscal-period-open retries are bound to the original tenant key and source hash. Both must be installed before runtime database privileges are treated as production-ready.

After installation, prove with the actual runtime login that supported reads and writes work for its tenant, another tenant is inaccessible, the login is not a migration owner / superuser / `BYPASSRLS`, and direct SQL cannot bypass journal immutability or period controls.

## Concurrency and hot-write operations

The multithreaded HTTP server gives each request an independent PostgreSQL
transaction. Each new session bounds lock waits to five seconds and idle
transactions to sixty seconds. State-changing proposal, adjusting, reversal,
HomeTax, period-open, and period-close commands acquire tenant-scoped
transaction advisory locks. Posting/reversal re-read their selected period
after acquiring the shared period lock; close selects the period row with
`FOR UPDATE` before evaluating its package. A lock timeout rolls back the
transaction and must be retried after the operator resolves the competing
command.

Migration `0006_concurrency_hot_partition.sql` adds tenant-leading indexes to
the high-write proposal, journal, line, reversal, receipt, HomeTax, and outbox
populations. Monitor `pg_stat_activity`, `pg_locks`, lock-wait duration, and
index usage before introducing physical hash-by-tenant/time partitions. A
partition migration must preserve every tenant-scoped primary/unique key,
foreign key, RLS policy, idempotency decision, and outbox ordering invariant.

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

Posting, reversal and close commands commit their outbox evidence in the same accounting transaction. Publishing an outbox row records publication state; it does not rewrite the accounting fact. Audit reads include published and unpublished evidence but do not publish rows themselves.

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
