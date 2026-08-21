# Security and Threat Model Baseline

## Protected assets

- posted journal headers, lines and reversal lineage;
- accounting policy and effective chart-account mappings;
- legal-entity, accounting-book and fiscal-period authority;
- immutable source hashes, command identities and evidence references;
- posting receipts, close snapshots and transactional outbox events;
- necessary accounting PII and access / export audit evidence;
- purpose-limited tax and database credentials.

## Trust boundaries

Source proposals are untrusted evidence until schema, tenant, source authority, payload identity, idempotency, balance, period and accounting-policy checks succeed. Metering / billing can submit proposals but cannot write accounting tables, choose final chart accounts or claim statutory posting.

Model or LLM output is untrusted proposal / interpretation only. It cannot post journals, approve reconciliations, alter accounting policy, authorize close or gain database-role membership.

Database administration is not business posting authority. Migration owners and break-glass identities are separate from ordinary application runtime identities.

## HTTP caller boundary

`X-CWL-Tenant-Reference` binds the request to the configured AIS tenant. It is not a bearer credential or general authorization decision. The standalone runner defaults to the loopback address `127.0.0.1`; an operator must explicitly request any non-loopback bind. A non-loopback listener must sit behind a trusted caller-authentication boundary. That host / gateway validates the caller before traffic reaches AIS and must fail closed when validated tenant identity and AIS tenant binding differ.

Purpose-bound application authorization is a separate control from PostgreSQL privileges. Request-body fields, model text, headers supplied by an untrusted client and database GUC values cannot grant posting, reversal, close or tax authority.

## PostgreSQL runtime identities

Production runtime access uses a non-owner, non-superuser, non-`BYPASSRLS` login with only the table / schema privileges required by supported application paths. Tenant-scoped authoritative tables both enable and `FORCE ROW LEVEL SECURITY`; the runtime identity is still deliberately non-owner so ordinary service access never depends on owner-bypass semantics. Real PostgreSQL integration tests must prove an actual restricted login can execute a supported same-tenant posting/read path while cross-tenant rows remain invisible and the login is neither owner, superuser nor `BYPASSRLS`.

Do not run the application as the migration owner. Keep administrative / break-glass credentials out of normal service configuration.

## Soft-close capability role

`accounting_closing_writer` is a `NOLOGIN` capability role owned by the database control plane. Migration `0005_closed_period_guard.sql` reasserts `NOLOGIN` even if the role pre-existed as a login role.

A soft-closed journal insert requires both:

1. a transaction-local `accounting_core.journal_write_role` classification of `period_closing`, `adjusting` or `reversal`; and
2. `session_user` membership in `accounting_closing_writer`.

The GUC alone is never authority.

Grant the capability only to the purpose-limited session login used for an approved close / adjustment / reversal path:

```sql
GRANT accounting_closing_writer TO <purpose_limited_closing_login>;
```

Ordinary proposal-ingest and read-only runtime identities must not receive this membership. Revoke the capability when the operational authorization ends:

```sql
REVOKE accounting_closing_writer FROM <purpose_limited_closing_login>;
```

Record grant and revoke actions as control evidence. Do not rely on `SET ROLE` from an unrelated generic login to manufacture business authority; the guard evaluates the session login's membership.

## Ledger integrity controls

- Money uses exact decimal arithmetic; binary floating-point values are rejected at the contract boundary.
- PostgreSQL deferred constraint triggers require every durable journal to contain lines and to balance exactly at commit.
- Posted journal facts are append-only. Corrections use reversal and separately posted replacement when required.
- Database mutation guards reject update/delete of finalized journal, line, source, reversal, receipt and proposal evidence. Once a posting receipt exists, late line or source-reference inserts into that journal also fail closed; initial posting writes those populations before issuing the receipt.
- Repository migration checks reject destructive journal mutation patterns, while database immutability controls are the authoritative runtime boundary.
- Hard-closed periods reject later journal inserts.
- Reversal accounting dates cannot precede the original accounting date.
- Command replay uses tenant-scoped identity plus immutable command / source evidence; changed evidence under the same identity fails closed.

## Billing egress / SSRF controls

Billing pull destinations are operator configured. `BILLING_BASE_URL` and optional `BILLING_ALLOWED_ORIGINS` define the only candidate origins. A request body cannot authorize a new destination.

Origin parsing fails closed on malformed host, port or IPv6 forms. `localhost`, loopback and link-local addresses are rejected even when placed in the allowlist. No input-controlled `file://` or arbitrary network target is allowed.

## Tax credential controls

`ACCOUNTING_HOMETAX_CREDENTIAL` is a purpose-limited AIS secret. Check presence only; never log, store in journal evidence or echo it in a receipt. The current foundation has no network HomeTax transport and never claims a transmitted filing.

## PII controls

Necessary PII must remain usable for authorized accounting work. Protect it with purpose-bound authorization, least privilege, encryption, tenant / context isolation, retention policy and immutable access / export logging rather than blanket masking that makes accounting operations impossible.

Generic journal / event contracts should use opaque master-data references when the full person / account value is not required. Do not place card numbers, CVC, passwords, bearer tokens, provider API secrets, model prompts or model responses in accounting contracts or logs.

## Availability and parser controls

Request and imported-artifact sizes are bounded before unbounded allocation. Invalid content length, malformed decimal, malformed URL, hostile XML or other parser errors must be converted to deterministic client / import failures rather than raw exceptions or partial persistence.

Remote Billing pagination is bounded and detects non-advancing cursors. A later-page remote failure never rewrites already committed accounting facts; retry relies on idempotent posting.

## Audit and evidence

Posting, reversal and close commands atomically persist authoritative evidence and transactional outbox events. Publishing outbox evidence does not mutate the accounting fact. High-impact operations should preserve principal / purpose / policy-decision evidence without storing raw credentials.

SOC 2 and CSAP are evidence-readiness design targets only. Do not claim certification or compliance without the corresponding external and operational evidence.

## Release security gate

Security is passing only when one unchanged protected source head has applicable SAST / security checks, PostgreSQL restricted-runtime tests, 100% owned production statement / branch coverage, package / SBOM / provenance evidence and qualifying independent review all passing. Queued, stale, predecessor, skipped or model-only evidence is non-passing.
