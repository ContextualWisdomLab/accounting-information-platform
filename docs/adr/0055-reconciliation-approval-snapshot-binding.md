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

There is a second ordering hazard: if `approved` evidence is recorded before
any statement/journal allocations exist, the immutable approval row freezes
later allocation insertion while the terminal approval guard still requires a
non-empty balanced allocation population. That strands the proposed match from
ever reaching `approved` under the reviewed snapshot. Rejection is different:
it consumes no source capacity and may legitimately record why an operator
rejected a candidate before allocating it.

There is also a lock-order hazard at the PostgreSQL boundary. A terminal match
`UPDATE` owns its `reconciliation_match` row before review triggers execute. An
allocation `INSERT` must not acquire the per-match snapshot advisory lock first
and then wait for that parent row, because the terminal transaction can then
wait for the advisory lock and complete a row/advisory deadlock cycle.

Reconciliation evidence remains separate from the statutory journal authority.
It may explain a bank-to-book decision, but it may not post, reverse, close a
period, select a chart account, or change accounting policy.

## Decision

Migration `0016_reconciliation_approval_evidence.sql` stores two distinct
identities:

- `source_payload_hash` and `source_payload_reference` identify the immutable
  approval command evidence supplied by the caller and retained in object
  storage;
- `reconciliation_snapshot_hash` and version `1` are computed by PostgreSQL
  from the tenant, run, match, candidate, and ordered statement/journal
  allocation rows.

The database-owned snapshot uses PostgreSQL 18's built-in SHA-256 binary-string
function and length-prefixed text fields, so references containing delimiters
cannot create an ambiguous canonical representation. A `BEFORE INSERT`
trigger overwrites any caller-supplied snapshot value with the current database
snapshot and fails when the candidate cannot be found.

Approval, allocation, and terminal match transitions share the same
transaction-level advisory lock for the tenant/run/match identity. Allocation
inserts and terminal match updates additionally serialize on the parent
`reconciliation_match` row and acquire those locks in one database-owned order:
parent row first, snapshot advisory lock second. Approval-evidence insertion
takes only the snapshot advisory lock and never subsequently waits on the parent
row, so it cannot complete a row/advisory wait cycle. Before an `approved`
decision row can be recorded, PostgreSQL requires at least one statement
allocation, at least one journal allocation, and exact equality of their
Decimal totals. The check runs while holding the same snapshot lock, so a
concurrent allocation command cannot cross the approval-evidence boundary. A
`rejected` decision row may be recorded without allocations because it does not
consume source capacity and the terminal rejected transition has no balanced
allocation requirement.

Once an approval row exists, later candidate retargeting and allocation inserts
fail closed. A proposed match may become `approved` or `rejected` only when a
same-decision immutable approval row exists and its stored snapshot hash equals
the current database snapshot. Reviewed terminal states cannot rewrite their
identity or approval timestamp, reopen, or switch decision; supersession
remains the explicit historical retirement path. The migration refuses to
install when pre-existing non-proposed matches have no durable approval row,
because their original review cannot be reconstructed safely; it does not
manufacture historical approval evidence during upgrade.

If a later terminal transition fails a source-capacity check after approval
evidence has already committed, the approval row remains immutable audit
evidence. It is not rewritten, deleted, or silently reused. The operator must
supersede that reviewed match, start a new reconciliation run with a new
candidate and proposed match, and review the new snapshot. This recovery path
preserves the immutable-history model rather than pretending that a second
match can reuse the same candidate identity in the original run.

## Consequences

The approval evidence cannot silently drift away from the monetary population
reviewed by the operator, including when approval and allocation commands race
in separate PostgreSQL transactions. An operator who attempts to record
`approved` evidence too early receives the next action: add or correct the
statement/journal allocations, then record the approval evidence again. A
rejected candidate can still retain durable review evidence before allocation.
A later capacity conflict after durable review evidence instead leads to the
explicit supersede/new-run recovery above. Reconciliation allocation evidence
stays append-only and tenant-scoped. A rejected or superseded match preserves
its historical evidence and releases active source capacity according to the
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

A later regression covers the operator-ordering boundary directly: `approved`
evidence fails closed before a complete balanced allocation population exists,
then succeeds after balanced statement/journal allocations are recorded; the
same regression proves `rejected` evidence remains valid without allocations.

The lock-order regression uses two real PostgreSQL connections and
`pg_blocking_pids()` as a barrier rather than a timing sleep. It first reproduced
the row/advisory deadlock when allocation insertion acquired the snapshot lock
before the parent row. The repaired allocation path acquires the parent match
row before the snapshot advisory lock, so an invalid terminal transition fails
with the stable accounting-control error and the waiting valid allocation then
continues instead of receiving `deadlock detected`.

## References

See `docs/doctoring/REFERENCES.md` and the PostgreSQL 18 entries in
`docs/doctoring/STANDARD_TRACEABILITY.md` for the SHA-256, trigger, advisory-lock,
row-lock, and transaction-isolation basis of this decision.
