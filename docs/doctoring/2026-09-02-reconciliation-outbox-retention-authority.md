# Reconciliation outbox retention authority — 2026-09-02

## Problem

Migration `0021_reconciliation_exception_resolution_outbox_pair.sql` made command, terminal accounting state, and the matching outbox event commit atomically. That commit-time constraint did not protect the relationship afterward. A privileged SQL writer could delete the already-committed reconciliation outbox row or re-key its tenant/type/reference/hash identity, leaving immutable reconciliation command authority behind while its bound delivery/audit evidence disappeared.

The supported outbox publication boundary does not require either operation. Publication changes only `published_at`; the tenant, event type, aggregate reference, payload reference, and payload hash remain the event identity.

## Constraints

- Preserve ordinary `publish_outbox_event` semantics and idempotent `published_at` updates.
- Do not make every accounting outbox event globally immutable as an incidental child-PR policy expansion.
- Protect both maker-checker exception-resolution authority and reconciliation-run lifecycle authority.
- Allow an explicitly controlled same-transaction replacement only when the final database state still contains exactly one matching event.
- Fail a forward migration over a database whose reconciliation authority was already detached after migration 0021; a new trigger must not bless damaged provenance.
- Keep PostgreSQL, not application convention, as the final invariant owner for direct SQL and privileged maintenance paths.

## Alternatives considered

1. **Application-only delete/update checks.** Rejected because privileged SQL and future maintenance jobs can bypass the application boundary.
2. **Block all updates to `accounting_integration.outbox_event`.** Rejected because publication legitimately updates `published_at` and ADR 0017 defines that operation as the supported delivery acknowledgement.
3. **Make the entire outbox append-only in this slice.** Rejected as a broader accounting-platform decision that needs its own repository-wide lifecycle/retention ADR and migration plan.
4. **Only add future-row triggers.** Rejected because a database damaged between migrations 0021 and 0022 would install successfully while already violating the intended authority contract.

## Selected repair

The RED regression commit `a82e5c20eb4df07521d628d1b39e119ce7dd2ac5` added real PostgreSQL tests requiring a committed exception-resolution outbox row to survive later DELETE and identity re-key attempts.

Migration `0022_reconciliation_authority_outbox_retention.sql` then added two deferred outbox-side guards. They run after DELETE and after updates to `tenant_account_id`, `event_type_code`, `aggregate_reference`, `payload_reference`, or `payload_hash`. When the old row was bound to an immutable reconciliation exception-resolution command or lifecycle-transition command, the transaction may commit only if exactly one matching outbox row remains. `published_at` is deliberately excluded, so publication remains available.

The same migration performs a forward preflight over all existing reconciliation exception-resolution and lifecycle commands. Installation fails with `reconciliation_authority_outbox_retention_preflight` if any command lacks exactly one matching event. Operators must restore or reconstruct verified provenance before continuing; the migration does not synthesize evidence.

The canonical public installer now requires 0022 after the parent database-authority overlay, 0020, and 0021. Loader tests were corrected to model that complete chain rather than accidentally failing earlier on omitted required migrations. Lifecycle retention coverage was also added so both command families exercise the new database invariant.

## Failure and operations scenarios

- **Normal publisher:** updating only `published_at` succeeds; accounting authority and event identity are unchanged.
- **Accidental cleanup DELETE:** commit fails when the row is bound to a reconciliation authority command.
- **Identity re-key:** changing linkage fields fails unless the same transaction leaves exactly one event matching the immutable command.
- **Damaged upgrade source:** migration 0022 refuses installation when a pre-existing command has zero or multiple matching events. Restore verified provenance or stop the upgrade; do not fabricate an event merely to satisfy the check.
- **Unrelated outbox rows:** this migration does not grant new semantics or retention policy to posting, reversal, period-close, or other event families.

## Evidence boundary

Relevant commits in the current writer lineage are:

- `a82e5c20eb4df07521d628d1b39e119ce7dd2ac5` — RED PostgreSQL retention regression.
- `b754a620240e7a3cc101be0f834b711be652fe54` — production outbox retention migration.
- `f0932938e4ab8f4cffb755f11cf1a16916a23ffd` — canonical installer includes migration 0022.
- `6e5bf8e2d212b82d17547ebfb10e2233eaf7232d` and `ccbdff4696e4d486c1003ff2f54c3621bfa8e214` — migration-loader contract repairs.
- `80a68036347a5aa9b22bd886347a306e77f72819` — lifecycle-side retention regression coverage.
- `3ce802d59b9cc46b40fa0e43f5faba6ecbd219f5` — existing-authority migration preflight.

These commits are mutable PR evidence, not protected-branch or release evidence. Exact-head PostgreSQL, coverage, security, dependency, review, package/SBOM/provenance, and parent-stack integration gates must still pass before this authority can be described as shipped.

## Follow-up

A future repository-wide outbox retention/archival design should decide whether non-reconciliation event families require the same immutable linkage or a separate durable archive table. That decision must preserve audit lineage, tenant isolation, publication state, backup/restore behavior, and any statutory retention obligations without turning this reconciliation repair into an undocumented global policy change.
