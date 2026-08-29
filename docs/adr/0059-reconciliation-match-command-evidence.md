# ADR 0059: Reconciliation match command evidence

## Status

Accepted in the current integration tree; protected-branch integration remains governed by the repository merge controls.

## Context

The reconciliation engine and migrations already define candidates, proposed
matches, exact statement/journal allocations, conservation guards, and later
human approval evidence. The system still lacked a public command boundary that
persisted one candidate and its source evidence atomically. A caller that wrote
those relations independently could leave a candidate without durable command
identity or make a retry ambiguous.

## Decision

Migration `0020_reconciliation_match_command_evidence.sql` adds the immutable,
forced-RLS `accounting_core.reconciliation_match_command` relation. It binds a
tenant, evaluating reconciliation run, candidate, and match to a tenant-scoped
candidate idempotency key, canonical command hash, source-payload hash, and
immutable object-storage reference. Composite foreign keys and uniqueness rules
prevent cross-scope evidence and duplicate command or match identities.

`accept_reconciliation_match` is the smallest durable command boundary: it
accepts one exact 1:1 proposed match, requires quoted positive equal decimal
amounts that equal the bound immutable bank-entry and posted-journal source
facts, creates the candidate, proposed match, statement allocation, journal
allocation, and command evidence in one transaction, and grants no approval,
close, chart-account, reversal, or posting authority. Exact retries return the
stored document; reuse of the key with changed command or source evidence fails
closed. Database source-conservation violations are translated to a stable
validation failure rather than leaking a driver error. `POST
/reconciliation-matches` exposes that command and
`GET /reconciliation-matches?reconciliation_match_id=` reads the tenant-scoped
document.

The command intentionally does not replace the existing pure split/aggregate
allocation planner or the database approval workflow. It provides a bounded
buyer-facing persistence seam for the common 1:1 case; many-to-many planning,
human approval, and any accounting adjustment remain separate evidence and
authority boundaries.

## Consequences

Proposed reconciliation evidence now has a durable retry identity and an
atomic candidate-to-allocation provenance chain. Legacy candidates or matches
without command evidence are not synthesized by this read boundary. A proposed
match remains non-authoritative: only the existing approval controls can record
a human decision, and any adjustment must re-enter the authoritative journal
command boundary.

## References

See `docs/DATA_MODEL.md`, `docs/OPERABILITY.md`, and
`docs/doctoring/STANDARD_TRACEABILITY.md` for the relational, operational, and
standards traceability contracts.
