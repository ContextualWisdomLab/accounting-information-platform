# Security Baseline

## Protected assets

- posted journal and reversal lineage;
- accounting policy and chart-account mappings;
- legal-entity and book assignments;
- source payload hashes and evidence references;
- fiscal-period close authority;
- posting receipts and reporting snapshots.

## Trust boundaries

- Source proposals are untrusted evidence until schema, authority, hash, idempotency, balance, and policy checks succeed.
- Billing cannot write journal tables directly.
- AI systems may explain or propose classifications but cannot approve policy, open periods, map chart accounts, or post journals.
- Database administrators do not automatically receive business posting authority.

## Controls

- OIDC/JWT audience and signature validation through Keyverse in the API milestone.
- Tenant-scoped composite foreign keys and PostgreSQL row-level security.
- Maker-checker approval for policy changes, period close/reopen, high-risk manual journals, and reversal outside normal operations.
- Append-only audit events and immutable source payload storage.
- Secrets in KMS or an approved secret store; no credentials in journal payloads or logs. `ACCOUNTING_HOMETAX_CREDENTIAL` is a purpose-limited AIS secret: check presence only, never log it, and never echo it on a HomeTax receipt.
- Bounded payload sizes and line counts, strict decimal parsing, and controlled event replay.
- Backup, restore, point-in-time recovery, and evidence-integrity rehearsal before release.

## Explicit exclusions

The platform does not store card numbers, CVC values, PAT plaintext, provider API secrets, LLM prompts, or LLM responses in accounting contracts. PII required for authorized accounting work is kept in purpose-bound master-data services and referenced by opaque identifiers rather than broadcast through events.
