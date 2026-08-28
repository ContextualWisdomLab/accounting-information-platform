# ADR 0055: Database-owned reconciliation approval snapshot binding

- Status: Proposed
- Date: 2026-08-28

## Context

An approval command hash proves the identity of the command submitted by an
operator; it does not prove which candidate and allocation rows the operator
reviewed. A proposed reconciliation match can otherwise receive approval
evidence and then acquire additional allocation rows before its terminal state
transition. That would make a valid-looking approval authorize a different
monetary population.

Reconciliation evidence remains separate from the statutory journal authority.
It may explain a bank-to-book decision, but it may not post, reverse, close a
period, select a chart account, or change accounting policy.

## Decision

Migration `0016_reconciliation_approval_evidence.sql` stores two distinct
identities:

- `source_payload_hash` is the immutable SHA-256 hash of the approval command
  evidence supplied by the caller;
- `reconciliation_snapshot_hash` and version `1` are computed by PostgreSQL
  from the tenant, run, match, candidate, and ordered statement/journal
  allocation rows.

The database-owned snapshot uses PostgreSQL 18's built-in SHA-256 binary-string
function and length-prefixed text fields, so references containing delimiters
cannot create an ambiguous canonical representation. A `BEFORE INSERT`
trigger overwrites any caller-supplied snapshot value with the current database
snapshot and fails when the candidate cannot be found.

Approval, allocation, and terminal match transitions take the same
transaction-level advisory lock for the tenant/run/match identity. Once an
approval row exists, later allocation inserts fail closed. A proposed match
may become `approved` or `rejected` only when a same-decision immutable approval
row exists and its stored snapshot hash equals the current database snapshot.
Reviewed terminal states cannot reopen or switch decision; supersession remains
the explicit historical retirement path.

## Consequences

The approval evidence cannot silently drift away from the monetary population
reviewed by the operator, including when approval and allocation commands race
in separate PostgreSQL transactions. Reconciliation allocation evidence stays
append-only and tenant-scoped. A rejected or superseded match preserves its
historical evidence and releases active source capacity according to the
existing conservation controls.

The design intentionally does not add an application-side hash calculator or a
new approval service. The database is the authority for the persisted snapshot;
the current repository has no public approval HTTP command to extend in this
slice.

## Verification

The initial RED migration contract was run against exact parent head
`c0af868004dabde4a99205271dc77d502abfa9d9` and failed because migration 0016
did not exist. The PostgreSQL 18 regression then proved that a forged snapshot
value is overwritten, the stored hash equals the live database function, late
allocation fails closed, and the valid reviewed match can still become
`approved` after its allocation population is fixed.

## References

See `docs/doctoring/REFERENCES.md` and the PostgreSQL 18 entries in
`docs/doctoring/STANDARD_TRACEABILITY.md` for the SHA-256, trigger, and
transaction-isolation basis of this decision.
