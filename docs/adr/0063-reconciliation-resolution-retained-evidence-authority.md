# ADR 0063: Exception resolution binds to retained reconciliation evidence

- Status: Proposed
- Date: 2026-09-02
- Bounded context: Bank Reconciliation / Evidence and Audit
- Extends: ADR 0062 maker-checker exception-resolution command authority
- Depends on: migration 0013 reconciliation evidence registry and migration 0020 exception-resolution command

## Context

ADR 0062 made terminal reconciliation-exception state depend on a named immutable maker-checker command, but the command still accepted `resolution_evidence_reference` and `resolution_evidence_hash` as caller-supplied values. Syntax validation of a CWL reference and a `sha256:` digest does not prove that the reviewed artifact is retained by the Accounting Information Platform, belongs to the same tenant/run/exception, has the expected evidence role, or existed by the decision boundary.

That gap is an accounting-authority defect rather than a presentation defect. A fabricated reference plus a hash-shaped value could otherwise become part of an immutable resolution-command hash and, once the exception became terminal, contribute to run-finalization eligibility. Provenance metadata is not provenance proof.

Migration 0013 already owns the normalized `accounting_core.reconciliation_evidence` registry with tenant/run/exception scope, evidence type, evidence reference, payload hash, valid/effective time, database recording time, and forced tenant row-level security. The correct authority boundary is therefore local to Accounting: resolution must bind to retained AIS evidence instead of inventing a parallel foreign evidence store or trusting caller assertions.

## Decision

`accounting_core.reconciliation_exception_resolution_command` stores the database-resolved `reconciliation_evidence_id` in addition to the caller-visible evidence reference and digest. The row has a foreign key to `accounting_core.reconciliation_evidence`. The reference and digest remain in the incoming command because they are part of caller intent, idempotency identity, and the immutable receipt, but they do not themselves grant authority.

Before the command is accepted, PostgreSQL independently requires exactly the retained evidence row matching all of these facts:

- the bound `tenant_account_id`;
- the same `reconciliation_run_id`;
- the same `reconciliation_exception_id`;
- `evidence_type_code = 'exception_resolution_review'`;
- the exact `resolution_evidence_reference`;
- the exact `resolution_evidence_hash` as `evidence_payload_hash`.

The retained artifact must also satisfy temporal causality: its `effective_at` cannot be later than the resolution command's `effective_at`, and its database `recorded_at` cannot be later than the resolution command's database-owned `recorded_at`. The command trigger assigns the resolved evidence id and includes it in the database-derived reconciliation-exception-resolution command hash. A caller cannot select a different retained artifact merely by supplying a syntactically valid reference or digest.

The application performs the same scoped lookup before insert to return buyer-useful fail-closed validation, but this is defense in depth only. The database trigger is the independent authority and rejects a direct SQL command insert that lacks matching retained evidence.

`accounting_core.reconciliation_evidence` becomes append-only under migration 0020. `UPDATE` and `DELETE` are rejected for retained reconciliation-evidence rows. The registry represents historical source/control evidence, not mutable workflow state; mutable operational decisions belong in named command/state tables. Freezing the retained artifact also prevents a successful immutable resolution command from later pointing at changed or deleted provenance.

The evidence binding is intentionally narrow. It does not grant journal posting, journal reversal, chart-account selection, period-close, tax-submission, accounting-policy, Billing, or foreign-service write authority. Exception resolution remains an evidence/control decision; any correcting journal remains a separate General Ledger command under its own authorization, idempotency, and posting invariants.

## DDD and persistence consequences

`reconciliation_exception` remains the exception control entity within the Bank Reconciliation supporting subdomain. `reconciliation_evidence` is retained evidence owned by the same bounded context. `reconciliation_exception_resolution_command` is immutable command evidence linking the reviewed decision to the retained artifact. This is not a Shared Kernel and does not authorize direct cross-service SQL.

The additional `reconciliation_evidence_id` is a durable relational identity rather than a denormalized copy of artifact contents. The existing reference and digest are retained because they are stable external/provenance identifiers and command-input facts. The database trigger verifies their equality against the normalized evidence row before the resolution can become authoritative.

## Transaction and trigger semantics

The evidence check runs in the same transaction as the resolution command, terminal exception status, and outbox event. PostgreSQL row-level `BEFORE INSERT` triggers may modify the row that will be inserted, so the authority trigger can assign `reconciliation_evidence_id` before the row's `NOT NULL` and foreign-key constraints are checked. Trigger execution is part of the same transaction as the triggering statement; an error in the trigger or statement rolls back their effects. These semantics make the database-owned binding a fail-closed gate rather than a best-effort application assertion.

The existing reconciliation lifecycle lock, fresh `REPEATABLE READ` authority transaction, SQLSTATE `40001` whole-transaction retry, database-owned resolution `recorded_at`, maker-checker separation, shared reconciliation idempotency namespace, atomic status/outbox pairing, and exact replay rules remain unchanged.

## RED → GREEN acceptance

The exact implementation must prove at minimum:

1. a well-formed but nonexistent evidence reference/digest is rejected with zero resolution-command, terminal-status, and outbox side effects;
2. evidence retained for a different exception cannot authorize the target exception;
3. a digest that differs from the retained artifact is rejected;
4. evidence whose effective time is later than the resolution decision is rejected;
5. evidence whose system recording time is later than the resolution decision boundary is rejected;
6. a direct SQL resolution-command insert with fabricated evidence is rejected independently by PostgreSQL;
7. one retained exception-scoped `exception_resolution_review` artifact with exact reference/hash and valid temporal scope permits the named maker-checker command;
8. the resulting resolution command stores the retained `reconciliation_evidence_id` and exact replay returns the immutable receipt;
9. after the command commits, `UPDATE` and `DELETE` against the retained reconciliation-evidence artifact are rejected;
10. all prior maker-checker, idempotency, lifecycle-lock, temporal, RLS, atomic outbox, migration, coverage, security, and package/provenance gates remain green on one unchanged exact head.

## Alternatives rejected

**Trust a syntactically valid SHA-256 value.** Rejected because digest shape proves neither artifact existence nor ownership/scope.

**Validate retained evidence only in Python.** Rejected because privileged SQL or another application path could bypass that validation and still create durable command authority.

**Copy the entire reviewed artifact into the command row.** Rejected because it duplicates retained evidence, weakens normalized ownership, and creates competing provenance records.

**Allow retained reconciliation evidence to remain mutable until referenced.** Rejected for the current schema because the registry is already the durable audit/source-evidence substrate and there is no separate mutable draft-evidence aggregate. A future need for mutable working material should use a distinct object and explicit promotion command rather than mutating retained evidence in place.

## Standards and control traceability

PostgreSQL 18 documents that row-level `BEFORE` triggers execute before the row operation and can return a modified row for `INSERT`/`UPDATE`; it also documents that triggers execute in the same transaction and trigger errors roll back the statement/transaction effects. Those database semantics support the implementation mechanism, not an accounting standard claim.

NIST SP 800-53 Rev. 5/Release 5.1.1 is used only as security-control design context for separation of duties, auditability, information integrity, and non-repudiation. It does not certify this implementation or confer accounting authority. IFRS standards do not prescribe this application-level bank-reconciliation evidence protocol.

### References

National Institute of Standards and Technology. (2020, updated 2023). *Security and privacy controls for information systems and organizations (NIST SP 800-53 Rev. 5 / Release 5.1.1)*. U.S. Department of Commerce. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final

National Institute of Standards and Technology. (2023, October 17). *NIST invites public comments on SP 800-53 controls and plans patch release 5.1.1*. https://csrc.nist.gov/News/2023/nist-invites-public-comments-on-sp-800-53-controls

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
