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

Immutable command provenance must also prove one real candidate-to-match chain,
not merely prove that a candidate row and a match row both exist in the same
tenant and run. Independent foreign keys permit a cross-pair combination in
which command evidence names candidate A and match B even though match B points
to candidate C. Because command evidence is append-only, that mismatch must be
rejected by the database before it can become durable provenance.

## Decision

Migration `0020_reconciliation_match_command_evidence.sql` adds the immutable,
forced-RLS `accounting_core.reconciliation_match_command` relation. It binds a
tenant, evaluating reconciliation run, candidate, and match to a tenant-scoped
candidate idempotency key, canonical command hash, source-payload hash, and
immutable object-storage reference. The candidate still has its own tenant/run
foreign key, while one database-owned composite foreign key binds
`(tenant_account_id, reconciliation_run_id, reconciliation_match_id,
reconciliation_candidate_id)` to the same composite identity on
`reconciliation_match`. The command therefore cannot combine a valid candidate
with a different valid match from the same run. Uniqueness rules additionally
prevent duplicate command or match identities.

The command also binds source admission to the run snapshot: statement booking
and value timestamps must be no later than the bank cutoff, and journal
accounting date must be no later than the book cutoff. The journal amount is
read from the run assignment's cash chart account rather than the journal-wide
total; a `CRDT` statement requires that line on the debit side and a `DBIT`
statement requires it on the credit side. The run row is locked through the
candidate and allocation writes so a concurrent run transition cannot race the
match.

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
document. The HTTP boundary reports malformed identifiers as `400`, absent
source evidence as `404`, state conflicts as `409`, and source-content or
conservation validation failures as `422`.

The database requires command insertion to observe exactly one statement and
one journal allocation with equal exact amounts, and rejects allocation rows
inserted after command evidence. These are evidence-integrity controls only;
they do not turn a proposed match into an approved accounting fact.

Historical match admission also requires the posted journal fact to be known by
the run's `knowledge_cutoff_at`; a backdated accounting date alone is not
enough. The source database driver is loaded only inside the database command
path so dependency-free public imports remain available, while the runtime
database boundary still reports its existing fail-closed driver error.

The command intentionally does not replace the existing pure split/aggregate
allocation planner or the database approval workflow. It provides a bounded
buyer-facing persistence seam for the common 1:1 case; many-to-many planning,
human approval, and any accounting adjustment remain separate evidence and
authority boundaries.

## Consequences

Proposed reconciliation evidence now has a durable retry identity and an
atomic candidate-to-allocation provenance chain. A database write that attempts
to cross-pair a candidate with a match referencing another candidate fails at
the relational boundary, even when every identifier is otherwise valid in the
same tenant and run. Legacy candidates or matches without command evidence are
not synthesized by this read boundary. A proposed match remains
non-authoritative: only the existing approval controls can record a human
decision, and any adjustment must re-enter the authoritative journal command
boundary.

## References

See `docs/DATA_MODEL.md`, `docs/OPERABILITY.md`, and
`docs/doctoring/STANDARD_TRACEABILITY.md` for the relational, operational, and
standards traceability contracts.
