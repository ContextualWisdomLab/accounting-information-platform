# Reconciliation resolution evidence binding — 2026-09-02

## Problem observed

Current-head review found that exception resolution treated a caller-supplied `resolution_evidence_reference` plus a syntactically valid `sha256:` value as durable maker-checker provenance without proving that the referenced reviewed artifact was retained in the Accounting Information Platform. Migration 0013 already provides the normalized `accounting_core.reconciliation_evidence` registry, so accepting an unverified caller assertion created a local authority gap: terminal exception state could be created without one retained tenant/run/exception-scoped reviewed artifact.

This is an accounting-control provenance defect, not a formatting defect. A hash-shaped string is metadata; it does not establish artifact existence, scope, digest equality, or temporal causality.

## RED evidence added first

The real PostgreSQL acceptance suite was strengthened before the production repair. The new cases require zero resolution-command rows, zero terminal-status changes, and zero matching outbox events when:

- the supplied review-evidence reference does not exist;
- the retained artifact belongs to a different exception;
- the supplied digest differs from the retained artifact digest;
- retained evidence becomes effective after the resolution decision;
- retained evidence has a system recording time after the decision boundary;
- privileged direct SQL attempts to insert a resolution command with fabricated evidence.

The positive path now retains an exception-scoped review artifact first, requires the immutable resolution command to store that artifact's `reconciliation_evidence_id`, preserves exact replay, and proves that the retained artifact cannot later be updated or deleted.

## GREEN implementation

Migration 0020 now makes PostgreSQL the independent authority. `reconciliation_exception_resolution_command` stores a non-null `reconciliation_evidence_id` foreign key. Its `BEFORE INSERT` authority trigger resolves exactly one `accounting_core.reconciliation_evidence` row using the command's tenant, run, exception, the explicit `exception_resolution_review` evidence type, exact evidence reference, and exact payload digest. It rejects absence or temporal mismatch before terminal state can be admitted and includes the database-resolved evidence id in the database-derived command hash.

The application path performs the same scoped evidence lookup before insert to return actionable validation rather than a raw database error, but that check is defense in depth. Direct SQL remains unable to manufacture authority because the database trigger repeats the binding independently.

Migration 0020 also makes `accounting_core.reconciliation_evidence` append-only by rejecting `UPDATE` and `DELETE`. The table is the retained source/control-evidence registry; mutating or deleting an artifact after it becomes command authority would invalidate historical provenance. Mutable working material, if introduced later, must be a separate object with an explicit promotion boundary rather than a mutation of retained evidence.

## Transactional rationale

PostgreSQL 18 documents that a row-level `BEFORE` trigger can modify the row that will be inserted, which permits the database to populate the resolved evidence id before row constraints are evaluated. PostgreSQL also documents that ordinary triggers execute in the same transaction as the triggering statement, and an error in the trigger or statement rolls back their effects. This supports a fail-closed database authority boundary for the command/status/outbox transaction rather than a best-effort application convention.

The existing run lifecycle advisory lock, fresh `REPEATABLE READ` transaction, SQLSTATE `40001` whole-transaction retry, maker-checker separation, shared reconciliation idempotency namespace, database-owned system time, deferred command/status pairing, and no-posting/no-close boundary are unchanged.

## DDD and security boundary

The Bank Reconciliation supporting subdomain owns `reconciliation_exception`, retained `reconciliation_evidence`, and `reconciliation_exception_resolution_command`. The reviewed artifact is a local retained evidence entity, while the resolution command is immutable decision evidence. Neither is a General Ledger posting command. No foreign service, Billing model, LLM output, bank statement row, or request payload gains journal-posting, reversal, period-close, tax, chart-account, or accounting-policy authority from this change.

NIST SP 800-53 is used only as control-design context for separation of duties, auditability, information integrity, and non-repudiation. No compliance or certification claim is made. NIST's 2023 patch-release notice stated that Release 5.1.1 included clarifications/minor edits and catalog updates; the implementation does not depend on a control identifier renumbering or on a certification interpretation.

## Exact-head evidence rule

These source changes are not GREEN merely because the causal repair is present. Real PostgreSQL behavior, the complete owned-production statement/branch and edge-case coverage gates, public docstring/repository contracts, SAST/security/dependency review, reproducible package/SBOM/provenance, and qualifying current-head review must all pass together on one unchanged head before this child may integrate into its parent. Predecessor, queued, skipped, model-only, or status-only results do not transfer.

## References

National Institute of Standards and Technology. (2020, updated 2023). *Security and privacy controls for information systems and organizations (NIST SP 800-53 Rev. 5 / Release 5.1.1)*. U.S. Department of Commerce. https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final

National Institute of Standards and Technology. (2023, October 17). *NIST invites public comments on SP 800-53 controls and plans patch release 5.1.1*. https://csrc.nist.gov/News/2023/nist-invites-public-comments-on-sp-800-53-controls

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
