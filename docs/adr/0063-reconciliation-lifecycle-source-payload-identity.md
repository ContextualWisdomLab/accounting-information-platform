# ADR 0063: Reconciliation lifecycle idempotency binds the complete received command

- Status: Proposed
- Date: 2026-09-03
- Bounded context: Bank Reconciliation / Evidence and Audit
- Depends on: ADR 0060 and migrations 0019–0025

## Problem

`reconcile_reconciliation_run()` previously treated a lifecycle idempotency key as the identity of four normalized fields: run, actor, purpose, and effective time. The JSON command may contain additional received members. Reusing the same key with a materially changed member such as request context could therefore replay the original immutable transition receipt even though the received command bytes represented a different request.

That is inconsistent with the repository-wide accounting rule that every command has both an idempotency key and immutable source-payload identity. The lifecycle snapshot and transition-command hash already protect database-owned accounting facts; they do not prove the identity of every member received at the API/library boundary. These are different provenance objects and must remain separate.

## Constraints

The repair must not move journal, ledger, reconciliation balance, review, exception, or close authority back to caller-shaped JSON. PostgreSQL remains authoritative for the reconciliation snapshot, statement and book populations, transition recording time, legal lifecycle state, command hash, and transactional outbox pairing. The source-payload digest is only received-command identity for replay/conflict decisions.

The command accepts JSON, not arbitrary Python object graphs. Tuple, set, bytes, non-string object keys, non-finite numeric values, or custom objects must fail before database work rather than acquire an unstable or implementation-specific identity.

Existing transition rows created before the new column do not retain the complete received command. Their missing source identity cannot be reconstructed from the normalized transition hash or current aggregate state without inventing provenance.

## Decision

Migration `0026_reconciliation_lifecycle_source_payload_identity.sql` adds immutable `source_payload_hash` to `accounting_core.reconciliation_run_transition_command`. The application computes SHA-256 over the complete strict-JSON command using deterministic object-key ordering and compact separators before opening the database transaction. Exact replay compares the persisted digest in addition to run, actor, purpose, and effective time. A changed formerly ignored member under the same idempotency key is therefore a conflict.

The database transition hash advances to the `reconciliation_run_transition_command:v2` domain and includes `source_payload_hash` together with the already database-derived reconciliation snapshot and population identities. The outbox continues to bind the database-derived transition-command hash; no caller source digest becomes a financial fact by itself.

Migration 0026 performs a forced-RLS-safe all-tenant preflight and aborts if any transition row already exists. This is intentional. A pre-0026 transition may remain valid under its original released schema, but upgrading it into the stronger source-identity contract requires separately reviewed historical evidence. The migration does not synthesize a digest or rewrite immutable transition history.

## Alternatives considered

**Compare only known command members.** Rejected because a future or integration-specific member can change while replay still succeeds, recreating the defect.

**Hash only the database-normalized lifecycle command.** Rejected because that proves normalized accounting-control semantics, not the identity of the complete received command.

**Backfill pre-0026 rows from current state.** Rejected because current state cannot establish what JSON was originally received.

**Store raw command JSON in the accounting tables.** Rejected. The immutable SHA-256 identity is sufficient for idempotency here; raw payload retention belongs in an approved evidence/object-storage boundary when required and must not expand PII retention by default.

## Consequences and risks

Exact replays are stricter: any received JSON-member change requires a new idempotency key. This improves forensic traceability but means clients must replay the same canonical JSON value population rather than rely on the server ignoring unrecognized members.

The SHA-256 digest is evidence identity, not authentication, authorization, signature, or accounting approval. A caller cannot obtain reconciliation, posting, period-close, or policy authority merely by supplying a payload whose digest is known.

The migration intentionally blocks upgrades with pre-existing lifecycle transitions until provenance is reviewed. Operators must preserve the old database for audit or execute a separately reviewed migration backed by retained request evidence; deletion or invented backfill is not an acceptable remediation.

## Verification

Acceptance requires the following on one unchanged exact head:

- the RED introduced at `e45c2e37cc500ebeb5bb1eb36a2ad585427eaed1` proves a changed previously ignored JSON member conflicts under the original key;
- focused unit tests prove exact replay returns the persisted source digest and Python-only/non-JSON structures fail before SQL;
- real PostgreSQL integration proves `source_payload_hash` is persisted, immutable, included in the database-derived transition command hash, and exact replay does not append a second transition or outbox event;
- migration installation fails closed when 0026 is missing and when unverifiable pre-0026 transition history is present;
- repository validation, exact 100% owned statement/branch coverage, public docstrings, SAST/security/dependency, package/SBOM/provenance, and current-head review gates pass together.

## References

National Institute of Standards and Technology. (2015). *Secure Hash Standard (SHS)* (FIPS PUB 180-4). https://doi.org/10.6028/NIST.FIPS.180-4

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Row security policies*. https://www.postgresql.org/docs/18/ddl-rowsecurity.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html
