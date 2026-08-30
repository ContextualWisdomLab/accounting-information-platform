# ADR 0058: Reconciliation run command evidence

## Status

Accepted in the current integration tree; protected-branch integration remains governed by the repository merge controls.

## Context

The reconciliation tables already preserve evaluated scope, candidates, allocations,
approvals, and close-package evidence, but there was no public command boundary
that durably explained which immutable bank statement opened a run. Creating a run
from caller-supplied scope alone would permit a source hash, assignment, or
idempotency claim to drift away from the statement evidence.

## Decision

Migration `0019_reconciliation_run_command_evidence.sql` adds the immutable,
tenant-scoped `accounting_core.reconciliation_run_command` row. The command stores
the run and statement identities, a tenant-scoped idempotency key, canonical command
hash, raw bank-statement artifact payload hash, and immutable object-storage
reference. The raw artifact hash is distinct from the normalized statement hash;
the caller must supply the former. Composite foreign keys, forced RLS, exact hash
checks, and an immutable trigger make the evidence database-owned.

Because `reconciliation_run` already uses forced tenant RLS before migration 0019,
the migration creates a transaction-scoped `FOR SELECT ... TO current_user`
visibility policy only for its upgrade preflight. Before runtime command guards are
installed, the preflight scans every historical run and refuses the migration with
`reconciliation_run_command_upgrade_required` if a run lacks durable command
evidence. The temporary policy and guard function are dropped before commit. The
migration does not invent an idempotency key, source hash, statement identity, or
other command provenance that cannot be reconstructed from authoritative retained
evidence.

`POST /reconciliation-runs` validates that the statement is persisted, its source
hash matches, and its bank-account assignment is active for the requested legal
entity, book, and bank cutoff. It opens only `evaluating` scope, records one
`bank_statement` evidence reference, and replays an exact command. Reuse of a key
with changed evidence fails closed. Every bank, book, and knowledge cutoff must
carry the canonical UTC timestamp grammar with `T` and either `Z` or `+00:00`;
timezone-naive, non-UTC, or equivalent noncanonical forms fail before
persistence and canonical command hashing.
After the command lock, an existing idempotency key is resolved from its stored
run evidence before live assignment validation, so an exact retry remains a
replay even if that assignment later closes or overlaps; changed request fields
still fail closed.
For a new run, every selected statement, artifact, bank account, assignment,
legal-entity, accounting-book, balance, entry, and entry-detail fact must have
been recorded no later than `knowledge_cutoff_at`. A deferred database trigger also requires exactly one
command row at commit and verifies that its statement belongs to the run's
assigned bank account.
`GET /reconciliation-runs` returns the same tenant-scoped run document.

Distinct idempotency keys may open distinct immutable runs for the same statement
and scope. This permits a later policy or cutoff evaluation to remain separately
auditable; only reuse of one key is an idempotency conflict.

This slice deliberately does not perform matching, allocate statement or journal
amounts, approve a match, close a fiscal period, select a final chart account, or
post a journal. Matching and review must consume the immutable run and statement
evidence through a later bounded command.

## Consequences

Run creation has a durable provenance root and an explicit retry contract. An
upgrade containing historical `reconciliation_run` rows without command evidence
fails before migration 0019 can commit; operators must reconstruct provenance from
authoritative retained evidence through an explicit repair decision or keep the
upgrade blocked. Runtime read paths therefore cannot silently hide a successfully
migrated historical run merely because no command row exists. The persisted command
remains a control fact and does not cross the commercial-billing/statutory-accounting
authority boundary.

## References

See `docs/doctoring/STANDARD_TRACEABILITY.md`, `docs/DATA_MODEL.md`, and the
immutable bank-statement evidence ADR 0052/0057.
